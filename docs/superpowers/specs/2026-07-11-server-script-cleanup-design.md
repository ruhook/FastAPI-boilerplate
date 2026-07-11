# Server 脚本清理设计

## 背景

`hr-server` 当前包含 30 个可执行 Python 脚本，约 1.66 万行。它们混合了承担正式运维职责的入口、手工 QA 数据 seed、API/浏览器回归套件、一次性导数程序和未被 pytest 收集的旧测试文件。CI 只执行 `pytest`、Ruff 和核心类型检查，因此手工回归脚本即使业务契约过期，也可能继续通过静态检查并长期留在仓库中。

2026-06-25 的最新 V2 seed 日志已经暴露这一问题：`seed_job_progress_demo_flow` 向使用国家字典的 `nationality` 字段提交了 `Brazilian`，接口返回 HTTP 400；V2 手工数据 seed、API 回归和完整回归都依赖该脚本。

## 目标

- 删除确定没有持续职责的一次性脚本、重复测试和被 V2 取代的旧流程。
- 保留一套有明确入口、依赖关系和文档的 V2 手工 QA/回归链路。
- 修复国家字典契约漂移，并以测试锁定演示申请数据必须使用字典合法值。
- 保留完整 Alembic 迁移链和必要的运维脚本。
- 不改变生产 API、数据库结构或线上启动流程。

## 方案比较

### 方案 A：只删除完全无引用文件

仅删除仓库内引用数为零的脚本。改动最小，但会保留未被 pytest 收集的重复测试、被 V2 覆盖的旧流程和已经确认失效的 V2 数据契约，不能恢复测试入口的可信度。

### 方案 B：分级删除，并收敛到 V2 主链路（采用）

删除确定遗留文件和两个重复旧流程；保留 V2 及其仍使用的共享 seed；修复当前已确认的契约错误；更新测试手册只推荐 V2 主入口。该方案在明显降低维护面积的同时，不重写现有 QA 基础设施。

### 方案 C：删除所有手工脚本并全部改写为 pytest

长期一致性最好，但会把浏览器、实时服务、导出下载和数据准备全部纳入本轮重构，范围过大，也会失去方便人工点页面的稳定 seed。本轮不采用。

## 删除范围

### 确定遗留

- `scripts/local_admin_bootstrap.test.py`
  - 文件名不符合 pytest 默认收集规则。
  - 相同安全行为已由 `tests/core/test_security_config.py` 覆盖。
- `src/scripts/backfill_referral_profiles.py`
  - 没有代码或文档入口。
  - `20260520_000032_referral_bonus_models.py` 已对迁移时的 Active 合同执行等价回填；现行业务服务负责后续合同产生的 referral profile。
- `src/scripts/import_haokang_data.py`
  - 一次性本地 Excel 导入，包含个人绝对路径，无仓库入口。
- `src/scripts/import_haokang_visible_payload.py`
  - 一次性服务器 payload 导入，包含固定服务器绝对路径，无仓库入口。
- `src/scripts/seed_ruanhaokang_candidate_jobs.py`
  - 个人账号演示数据，已被通用 Candidate Portal seed 覆盖，无仓库入口。

### 被 V2 取代的重复流程

- `src/scripts/run_recruitment_e2e_demo.py`
  - 仅被旧测试手册引用；招聘、合同和权限主链路已经由 V2 full regression 覆盖。
- `src/scripts/run_client_signed_contract_upload_demo.py`
  - 仅被旧测试手册引用；签回合同已由 Candidate Portal、API regression 和 batch contract suite 覆盖。

删除后仍可从 Git 历史恢复一次性导数逻辑，不为历史脚本维持当前业务模型兼容性。

## 保留边界

- 保留全部 `src/migrations/versions/*.py`。47 个迁移构成从 `20260403_000001` 到 `20260710_000047` 的线性链，不按普通数据脚本处理。
- 保留 `create_first_superuser.py` 和 `reset_local_state.py` 作为明确记录在 README/开发文档中的运维入口。
- 保留 `src/scripts/v2` 作为唯一推荐的综合回归入口。
- 保留 V2 当前依赖的 `seed_candidate_base_form_template.py`、`seed_job_progress_demo_flow.py`、`seed_timesheet_demo_flow.py`、`run_candidate_my_jobs_demo.py`、`run_advanced_filter_bulk_demo.py`、`create_assessment_reviewer.py` 及其共享 helper。
- 保留 `seed_preview_demo_data.py`，因为正式测试仍验证其表单数据契约。

## 契约修复

Candidate base form 的 `nationality` 和 `country_of_residence` 都绑定全局国家字典，值必须来自 `GLOBAL_COUNTRY_OPTIONS`。演示申请统一使用字典值 `Brazil`，不再为 `nationality` 提交形容词 `Brazilian`。

修改位置：

- `src/scripts/run_client_apply_demo.py::build_application_items`
- `src/scripts/seed_job_progress_demo_flow.py::build_application_items`

测试直接调用两个纯数据构造函数，提取两个国家字段，并断言值均为 `Brazil` 且存在于全局国家选项中。测试不连接数据库或服务。

## 文档与入口

`docs/testing-playbook-zh.md` 将：

- 使用 `src.scripts.v2.seed_manual_review_data` 作为手工页面验收的数据入口。
- 使用 `src.scripts.v2.run_full_regression_suite` 作为完整自动回归入口。
- 删除已移除旧流程的命令和说明。
- 保留仍有独立价值的 Candidate 报名和测试题上传单点脚本说明。

V2 README 继续作为各专项套件的详细说明，不新增第二套入口文档。

## 验证策略

1. TDD：先新增国家字典契约测试并确认它因 `Brazilian` 失败。
2. 最小修复两个数据构造函数，确认新增测试通过。
3. 运行 `tests/scripts` 与 candidate field 相关测试，确认保留脚本的导入和纯逻辑测试通过。
4. 运行完整 `pytest`。
5. 运行 Ruff、核心 mypy 和 Alembic heads，确认静态质量门、迁移 head 与 CI 配置一致。
6. 搜索已删除模块名，确认不存在活跃代码、README、测试手册或 CI 引用。

实时 V2 full regression 依赖本地 Web/Admin/前端、Redis 和数据库服务。本轮以可自动运行的测试覆盖已确认根因；如果完整实时服务未启动，交付时明确报告未执行该项，不以静态检查冒充端到端通过。

## 成功标准

- 七个目标脚本从工作树删除，文档不再推荐它们。
- V2 依赖图不包含已删除模块。
- 演示申请的两个国家字段均使用合法字典值。
- 新增回归测试经历明确的红—绿过程。
- 全量自动测试和 CI 对应静态检查通过；若环境阻止某项验证，报告具体阻塞而不是宣称通过。
