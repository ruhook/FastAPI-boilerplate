# 测试流程清单

## 1. 空库初始化

在 `hr-server` 下执行：

```bash
uv run python -m src.scripts.reset_local_state
cd src
uv run alembic -c alembic.ini upgrade head
```

说明：

- 第一条会重建本地 MySQL 数据库，并清空 `storage/assets`
- 第二条会把数据库结构重新迁移到最新版本

## 2. 启动服务

### 后端

在 `hr-server` 下分别启动：

```bash
uv run python run_web.py
uv run python run_admin.py
uv run python event_consumer.py
```

### 前端

在 `admin-web-next` 下启动：

```bash
npm run dev -- --port 3001
```

在 `candidate-web-next` 下启动：

```bash
npm run dev -- --port 3002
```

## 3. B 端完整联调

推荐脚本：

```bash
cd /Users/ruanhaokang/workspace/hr/hr-server
uv run python -m src.scripts.run_recruitment_e2e_demo
```

这条脚本会覆盖：

- 管理员和判题人准备
- 岗位与报名表模板准备
- 候选人注册与投递
- 测试题回收
- 判题人分配
- 判题结果更新
- 自动化流转
- 待签合同上传
- 候选人签回合同上传
- 公司盖章合同上传
- 邮件任务创建
- 人才日志 / 流程日志回查

默认账号：

- 管理员：`flowadmin / FlowAdmin123!`
- 判题人：`judgereviewer / JudgeReview123!`

跑完后建议人工检查：

- 后台登录是否正常
- `/jobs` 是否能看到演示岗位
- `/jobs/:jobId/progress?stage=assessment` 是否能看到测试题候选人
- 变更判题人后，右上角铃铛内部信是否出现
- 邮件模板、签名、发送历史是否能正常查看

## 4. C 端完整联调

推荐脚本：

```bash
cd /Users/ruanhaokang/workspace/hr/hr-server
uv run python -m src.scripts.run_candidate_my_jobs_demo
```

这条脚本会准备 7 个不同状态的岗位和申请：

- Pending Screening
- Assessment Review
- Screening Passed
- Contract Pool
- Active
- Rejected
- Replaced

并覆盖：

- 候选人注册 / 登录
- 岗位投递
- 同岗位不可重复投递
- 测试题多次上传
- 待签合同下载
- 签回合同上传
- `/me/applications` 分页 / 阶段筛选 / `needs_action_only`

默认候选人账号：

- `712696306@qq.com / 12345678`

跑完后建议人工检查：

- `http://localhost:3002/jobs`
- `http://localhost:3002/my-jobs`
- `Assessment Review` 详情里测试题上传是否可用
- `Contract Pool` 详情里待签合同下载和签回上传是否可用

## 5. 单点脚本

如果只想验证某一段链路，可以单独跑：

### 候选人报名

```bash
cd /Users/ruanhaokang/workspace/hr/hr-server
uv run python -m src.scripts.run_client_apply_demo
```

默认候选人：

- `demo.candidate@example.com / Candidate123!`

### 候选人上传测试题

```bash
cd /Users/ruanhaokang/workspace/hr/hr-server
uv run python -m src.scripts.run_client_assessment_upload_demo
```

### 候选人上传签回合同

```bash
cd /Users/ruanhaokang/workspace/hr/hr-server
uv run python -m src.scripts.run_client_signed_contract_upload_demo
```

## 6. 邮件测试说明

当前本地默认是走真实 SMTP。

如果只想看最终邮件内容、不想真实发出去，可以在 `src/.env` 加：

```env
MAIL_DELIVERY_MODE=preview
```

这样事件消费者会把最终主题、HTML 和附件信息直接打印到日志里。
