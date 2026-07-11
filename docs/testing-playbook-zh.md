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

## 3. 准备统一手工验收数据

在 `hr-server` 下执行：

```bash
uv run python -m src.scripts.v2.seed_manual_review_data
```

该入口会统一准备：

- 招聘流程、自动筛选和判题账号
- 合同、工时、收益和邀请奖励
- Candidate Portal 的 My Jobs / My Contracts 页面状态
- 后台与候选人端人工检查所需账号

脚本会在 `tmp/v2/` 生成 `manual-review-seed-v2-*.json`，其中包含实际账号、页面路径、seed 结果和各子脚本日志。人工验收以该文件输出为准，不在文档中维护第二套默认账号。

建议人工检查：

- Admin `/jobs`、招聘进度、合同库、人才库和工时页面
- 判题人权限隔离与右上角内部通知
- Candidate `/jobs`、`/my-jobs`、`/my-contracts`、`/working-hours`、`/referral` 和 `/earnings`
- 测试题上传、待签合同下载、签回合同上传和各状态详情

## 4. 完整回归

后端、前端和事件消费者都已启动后，在 `hr-server` 下执行：

```bash
uv run python -m src.scripts.v2.run_full_regression_suite
```

完整入口会串联 API、批量合同、权限矩阵、注册验证码、下载导出和浏览器 E2E。当前没有可用的 Chromium 环境时可加 `--skip-browser`；各专项入口和产物说明见 `src/scripts/v2/README.md`。

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

## 6. 邮件测试说明

当前本地默认是走真实 SMTP。

如果只想看最终邮件内容、不想真实发出去，可以在 `src/.env` 加：

```env
MAIL_DELIVERY_MODE=preview
```

这样事件消费者会把最终主题、HTML 和附件信息直接打印到日志里。
