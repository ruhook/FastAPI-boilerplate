# 业务分层与结算模型整体重构设计

## 背景

当前 server 的请求级事务、认证授权、可靠事件、模块目录和测试基础已经能够支撑业务开发。下一阶段的问题主要来自业务层：支付记录同时表示应付款和实际付款，工时与付款缺少稳定的结算边界，合同状态在 `ContractRecord` 与 `JobProgress` 之间重复，若干大型 `service.py` 同时承担查询、命令、计算、序列化和跨模块编排。

本次重构面向开发阶段，不迁移旧业务数据，不保留旧字段、旧状态、旧接口或旧 JSON 格式的兼容逻辑。涉及接口契约时，`hr-server`、`admin-web-next`、`candidate-web-next` 三端同步修改。

## 目标

1. 将应付款与实际支付流水拆成独立模型，并由数据库保证支付幂等。
2. 建立工时到应付款的可审计来源关系，为冻结和重算提供依据。
3. 明确合同、招聘流程、人才主档、工时和结算各自的数据所有权。
4. 增加轻量 application use-case 层，集中协调跨模块写入。
5. 将大型 service 按命令、查询、计算、工作流和序列化拆分。
6. 将参与状态流转、唯一约束、金额计算和高频筛选的 JSON 字段提升为类型化字段。
7. 增加金额、状态、数据库约束、并发、API 和前端契约测试。

## 非目标

- 不拆分微服务。
- 不引入通用 Repository 基类、消息总线框架或完整 DDD 基础设施。
- 不迁移开发数据库中的旧支付记录和旧 JSON 状态。
- 不保留旧 API 路径的转发入口，不做新旧字段双写。
- 不改变附件数据对普通账号开放的既定授权策略。

## 总体架构

项目继续采用模块化单体，调用方向固定为：

```text
API route
  -> application use case
    -> domain command / query / policy / calculator
      -> SQLAlchemy model
```

API 只负责认证授权、请求解析、调用 use case 和响应序列化。单模块读写由模块内部 command/query 完成；涉及两个及以上模块的业务流程由 `src/app/application/` 下的 use case 编排。领域模块不得直接修改其他模块拥有的记录。

请求事务仍由 `async_get_db` 管理。普通业务函数只允许 `flush`，不得自行 `commit` 或对整个 session 执行 `rollback`。后台 worker 使用独立 session 的现有边界保持不变。

## 模块所有权

### Payable

`payable` 唯一拥有应付款金额、币种、来源、结算周期和付款状态。工时、推荐奖励和合同模块只能提供计算输入，不能保存付款状态副本。

Payable 状态为：

- `pending`：可随源数据变化而重算。
- `processing`：付款处理中，来源被冻结。
- `paid`：已创建正式 Payment。
- `cancelled`：付款前取消，不能再支付。
- `reversed`：原 Payment 已冲正。

合法迁移为：

```text
pending -> processing -> paid -> reversed
pending -> cancelled
processing -> pending
processing -> cancelled
```

### Payment

`payment` 只保存真实资金流水。Payment 创建后不可编辑或软删除。支付信息录入错误时创建一条金额相反的 reversal Payment，并通过 `reversal_of_payment_id` 关联原流水。

人工付款也必须先创建 Payable，再确认形成 Payment，不再允许绕过应付款直接创建可编辑 Payment。

### ProjectTimesheetRecord

工时模块唯一拥有工时事实。工时增加 SQLAlchemy 乐观锁 `version`。被 `processing`、`paid` 或 `reversed` Payable 引用的工时禁止修改和删除；被 `pending` Payable 引用的工时可以修改，修改后同步重算受影响的 Payable。

### ContractRecord

合同模块唯一拥有合同审核、签署和有效状态。`JobProgress` 不保存合同状态副本，只通过关联查询读取合同状态，并通过 contracting application use case 发起合同动作。

合同字段采用稳定英文枚举值，中文只作为前端展示文案：

- `contract_status`: `pending_activation`, `active`, `expired`, `terminated`
- `contract_review_status`: `pending`, `changes_requested`, `approved`
- `signing_status`: `not_sent`, `sent`, `candidate_signed`, `company_sealed`

### JobProgress

招聘流程模块只维护招聘阶段、阶段时间和流程版本。候选人展示模型可以组合合同查询结果，但不能写入合同字段。旧 assessment 单附件回退、旧合同状态映射和旧 contract JSON key 全部删除。

### TalentProfile

人才模块只维护人才主档、申请合并结果和人才库运营字段。工时、合同、付款和日志聚合均通过专门 query service 读取，不在人才写命令中反向修改其他模块。

用于人才库筛选的 `status_override` 从 JSON 提升为可索引字段。其余纯展示快照和低频扩展信息可以继续保留在 JSON。

## 数据模型

### payable

核心字段：

- `id`
- `source_key`：非空、全局唯一、创建后不可变
- `payment_type`：`salary`、`team_leader_bonus`、`referral_reward`
- `status`
- `settlement_month`
- `user_id`
- `talent_profile_id`
- `contract_record_id`
- `referral_record_id`
- `company_id`
- `project_id`
- `amount`
- `currency`
- `calculation_snapshot` JSON
- 用户、公司、项目和合同文本快照
- `version`
- `processing_started_at`
- `paid_at`
- `cancelled_at`
- `created_by_admin_user_id`
- `updated_by_admin_user_id`
- 通用创建更新时间字段

`source_key` 使用稳定业务维度生成，不包含易变金额：

- 工资：`salary:{YYYY-MM}:{user_id}:{contract_record_id}`
- 组长奖金：`team_leader_bonus:{YYYY-MM}:{user_id}:{project_id}`
- 推荐里程碑：`referral_reward:{referral_record_id}:{milestone_index}`
- 人工应付款：`manual:{uuid}`

### payable_timesheet_source

字段：

- `payable_id`
- `project_timesheet_record_id`
- `source_version`
- `work_hours_snapshot`
- `amount_contribution_snapshot`

`payable_id + project_timesheet_record_id` 建立唯一约束。该表为工时结算审计来源，也用于判断记录是否冻结。

### payment

核心字段：

- `id`
- `payable_id`：非空，payment 与 reversal 都指向同一 Payable
- `entry_type`：`payment` 或 `reversal`
- `reversal_of_payment_id`
- `user_id`、合同、推荐、公司和项目关联
- `payment_type`
- `amount`
- `currency`
- `paid_at`
- `external_platform`
- `external_transaction_no`
- `remark`
- 付款时的用户、公司、项目、合同和推荐文本快照
- `created_by_admin_user_id`
- 创建时间

Payment 没有 `updated_by_admin_user_id`，API 不提供更新和删除操作。`payable_id + entry_type` 建立唯一约束，因此每个 Payable 最多一条普通 payment 和一条 reversal；`reversal_of_payment_id` 另建唯一约束，保证同一原 Payment 只被冲正一次。这两项都可直接由 MySQL 普通唯一索引实现。

### project_timesheet_record

增加 `version` 并配置为 SQLAlchemy `version_id_col`。不增加推测性的自然唯一键，因为同一用户、项目、日期、语言和工作类型允许存在多条真实工作记录；批量创建接口改用请求级 `idempotency_key` 表保证重复请求不会重复建行。

### contract_record

增加 `contract_review_status`、`signing_status` 类型化字段；`contract_status` 改为规范化枚举值。删除所有对 `data.contract_review`、`data.signing_status` 和旧英文标题状态的读取与写入。

### referral_record

删除 `paid_reward_amount`、`payout_status`、`last_paid_at`、`last_paid_by_admin_user_id`。推荐列表和汇总从 Payable 与 Payment 聚合这些值，ReferralRecord 只保存推荐关系、奖励模型快照和奖励上限。

## 结算数据流

### Payable 生成与重算

GET 请求不创建或修改 Payable。以下写流程同步调用 `settlement` application use case：

- 工时创建、更新、删除后，重算受影响用户、月份、合同和项目的 pending 工资及组长奖金。
- 推荐工时跨越里程碑后，为对应 `referral_record_id + milestone_index` 创建 Payable。
- 合同费率或组长配置变化后，重算仍为 pending 的受影响 Payable。

管理端另提供显式 `POST /v1/payables/sync`，按月份重建缺失的 pending Payable，供开发数据初始化、规则调整和运营修复使用。同步使用 `source_key` 唯一约束实现幂等；已进入 processing 之后不自动改金额。

### 确认支付

`POST /v1/payables/pay` 接收 Payable ID 列表和交易信息。每一项执行：

1. `SELECT ... FOR UPDATE` 锁定 Payable。
2. 验证状态为 processing，金额大于零且来源未变化。
3. 创建唯一普通 Payment。
4. 将 Payable 更新为 paid。
5. 写操作日志。

批量请求按项使用 savepoint，返回每项成功或失败结果。一项冲突不会回滚其他项。同一 Payable 使用相同参数重复确认时返回已有 Payment；金额、交易号或平台不同则返回 409。

### 冲正

`POST /v1/payments/{payment_id}/reverse` 锁定原 Payment 和 Payable，创建金额相反的 reversal，将 Payable 改为 reversed。已冲正 Payment 再次冲正返回既有 reversal；不同冲正参数返回 409。

### 候选人收入

候选人收入列表和汇总只查询 Payment。Pending 和 processing Payable 不计入已收入；管理端可以单独查看 Payable 汇总。

## API 契约

管理端使用：

- `GET /v1/payables`
- `POST /v1/payables/manual`
- `POST /v1/payables/sync`
- `POST /v1/payables/processing`
- `POST /v1/payables/reopen`
- `POST /v1/payables/cancel`
- `POST /v1/payables/pay`
- `GET /v1/payments`
- `POST /v1/payments/{payment_id}/reverse`

候选人端使用：

- `GET /v1/me/payments`

旧的 auto-payable、payment-record 更新、人工 Payment 批量创建和旧收入 API 删除。三端共享的字段名称全部采用 snake_case API 契约；两个 TypeScript 前端在 API client 边界定义明确响应类型，不在页面组件内猜测可选字段。

## 文件职责

### Server

- `src/app/modules/payable/model.py`：Payable 与工时来源关联模型。
- `src/app/modules/payable/schema.py`：Payable 请求和响应 DTO。
- `src/app/modules/payable/const.py`：类型和状态枚举。
- `src/app/modules/payable/calculator.py`：工资、组长奖金、推荐奖励纯计算。
- `src/app/modules/payable/commands.py`：单模块状态迁移和持久化。
- `src/app/modules/payable/queries.py`：数据库分页、筛选和汇总。
- `src/app/modules/payment/model.py`：不可变 Payment 模型。
- `src/app/modules/payment/commands.py`：支付与冲正写入。
- `src/app/modules/payment/queries.py`：管理端和候选人端付款查询。
- `src/app/application/settlement.py`：工时、推荐、合同到 Payable 的跨模块编排。
- `src/app/application/payouts.py`：确认支付和冲正编排。
- `src/app/application/contracting.py`：合同动作与招聘阶段协调。

现有模块拆分：

- `project_timesheet_record`: `commands.py`、`queries.py`、`analytics.py`、`serialization.py`、`team_leader_bonus.py`
- `contract_record`: `commands.py`、`queries.py`、`policy.py`、`serialization.py`
- `talent_profile`: `application_submission.py`、`merge.py`、`commands.py`、`queries.py`、`serialization.py`
- `job`: `commands.py`、`queries.py`、`policy.py`、`serialization.py`

旧 `payment_record` 模块和上述模块的旧大型 `service.py` 在所有调用方迁移后删除，不保留 facade。

### Admin Web

管理端新增明确的 Payable 与 Payment API 类型和 service。现有付款页面按“应付款操作”和“支付流水查询”拆分数据源，状态操作只针对 Payable，冲正只针对 Payment。合同筛选和展示改用规范化枚举字段。

### Candidate Web

候选人收入页面改用 `/me/payments`，不显示 pending Payable。求职进度和合同展示改用 server 返回的类型化合同字段，不再消费旧 contract JSON 字段。

## 事务与错误处理

- 请求级 session 是唯一事务所有者。
- 领域 command 不调用全局 rollback；需要隔离单项失败时使用 `begin_nested()` savepoint。
- 唯一约束、乐观锁和非法状态迁移统一转换为 409。
- 不存在的资源返回 404，字段和状态参数错误返回 422，权限不足返回 403。
- Payable、Payment、工时和合同的并发写入依赖数据库约束、行锁或 version，不依赖先查询再做 Python 判断。
- 邮件和其他外部副作用继续写 outbox，由事务提交后的 worker 发送。

## 测试策略

### 单元测试

- 工资、组长奖金和推荐奖励金额计算。
- source key 稳定性。
- Payable 状态迁移矩阵。
- Payment 冲正金额和关联规则。
- 合同审核、签署、激活状态规则。
- TalentProfile 合并策略与状态字段。

### MySQL 集成测试

- source_key 唯一约束。
- Payment 对 `payable_id + entry_type` 的唯一约束。
- 单 Payment 单 reversal 约束。
- Payable 与工时来源关系。
- processing/paid/reversed Payable 对工时的冻结。
- pending Payable 在工时变化后的重算。
- referral 汇总从 Payable/Payment 正确派生。

### 并发测试

- 两个 session 同时同步同一 source_key，只产生一个 Payable。
- 两个 session 同时确认同一 Payable，只产生一个 Payment。
- 支付与工时更新竞争时，不出现已付金额使用未冻结工时的情况。
- 两个 session 更新同一工时，后提交者得到 409。
- 两个 session 更新同一合同版本，后提交者得到 409。

### API 与前端测试

- 管理端 Payable 查询、同步、状态转换、批量支付、部分失败和冲正。
- 候选人收入只统计 Payment。
- 旧 API 返回 404，不存在兼容入口。
- admin 与 candidate API client 类型、页面状态和错误提示。
- 两个前端均运行单测、类型检查和生产构建。

## 实施批次

1. 建立独立 worktree、测试基线和新 Payable/Payment 模型。
2. 以 TDD 实现结算计算、持久化、幂等支付和冲正。
3. 接入工时版本、来源关联、冻结和重算。
4. 接入推荐奖励并删除 ReferralRecord 的付款副本字段。
5. 统一 ContractRecord 状态并移除 JobProgress 合同副本和兼容路径。
6. 拆分 TalentProfile、ProjectTimesheetRecord、ContractRecord 和 Job service。
7. 收薄直接操作 ORM 的 API，并将跨模块写流程迁移到 application use case。
8. 同步管理端和候选人端接口、类型、页面与测试。
9. 扩大 mypy 到本次涉及的业务模块，修复 SQLAlchemy 笛卡尔积警告。
10. 执行三端全量验证并按仓库分别提交可审查的原子提交。

## 并行窗口隔离

三个仓库分别从各自确认的基线创建同名 `refactor/business-layering-overhaul` 分支和独立 worktree。现有工作目录、现有功能分支和未提交文件不做任何修改。最终合并前分别更新目标分支并解决同文件冲突；接口契约提交先在 server 完成，两个前端提交引用同一份最终契约。

## 完成标准

- 旧 payment_record 模型、接口、JSON 付款状态和兼容逻辑已删除。
- Payable 与 Payment 数据库约束可以阻止重复应付款、重复支付和重复冲正。
- 工时冻结、乐观锁和结算来源审计可由测试证明。
- ContractRecord 是合同状态唯一来源。
- ReferralRecord 不再保存付款副本。
- 大型 service 完成职责拆分，跨模块写入集中在 application use case。
- server 全量 pytest、Ruff、涉及模块 mypy、Alembic 单头和锁文件检查通过。
- admin 与 candidate 的测试、类型检查和生产构建通过。
- 三个原工作目录中的其他窗口改动未被覆盖或混入本次提交。
