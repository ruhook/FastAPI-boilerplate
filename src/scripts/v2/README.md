# V2 Demo Seeds And API Regression

这套 `v2` 脚本把“手工联调用的数据准备”和“API 级回归验证”拆开了。

## 目录说明

- `shared.py`
  - 公共常量、HTTP 登录助手、Office 测试文件生成、子脚本调用与日志落盘。
- `seed_manual_review_data.py`
  - 给手工联调用的整包数据脚本。
  - 会串起岗位流程、候选人门户、合同/工时/收益/邀请奖励等现有 seed。
  - 输出一个聚合 JSON summary，方便直接查账号、页面、日志。
- `run_api_regression_suite.py`
  - 给自动回归用的 API 脚本。
  - 尽量走本地前端所调用的 HTTP API，不直接调 service。
  - 默认会先跑 `seed_manual_review_data.py`，再跑候选人 / Admin 的主链路检查。
  - 现在同时覆盖读链路、写链路和一批边界校验。
  - `--skip-seed` 时会自动读取最新一份 `manual-review-seed-v2-*.json`，适合刚刷新过数据后的快速复查；如果上一轮已经执行过写入型检查，建议不加 `--skip-seed` 重新刷新 seed。
- `run_batch_contract_mutation_suite.py`
  - 专门压“批量上传待签合同 / 批量通知签约 / C 端回签 / 批量审核 / 上传公司签回合同 / 自动入职”链路。
  - 后端当前没有单独 batch upload API，所以脚本按前端真实交互一样：本地做文件名匹配，再循环调用真实上传接口。
  - 会创建并重置 `batch.contract.v2.*@example.com` 测试候选人。
- `run_browser_smoke_suite.py`
  - 前端页面级 smoke。
  - 如果本机 Python 环境安装了 Playwright，会做真实浏览器登录态注入并打开核心页面。
  - 如果没有 Playwright，会自动降级为静态 HTTP SPA smoke + API 登录校验；需要强制真实浏览器时加 `--require-browser`。
- `run_browser_e2e_suite.py`
  - 真实 Chromium 浏览器 E2E。
  - 覆盖未登录重定向、Admin/C 端核心页面渲染、C 端横向滚动守卫、人才库和工时 CSV 下载按钮。
- `run_permission_matrix_suite.py`
  - Admin 权限矩阵。
  - 覆盖超管、普通业务账号、测试题判题账号、候选人 token、匿名访问。
- `run_register_verification_suite.py`
  - C 端注册验证码。
  - 默认写入测试验证码到 Redis 后走注册 API，避免依赖真实邮箱收件；如需真实 SMTP 发送，可加 `--include-real-send`。
- `run_export_download_suite.py`
  - 受保护附件下载与导出源数据。
  - 覆盖 C 端本人下载、跨用户拦截、Admin 下载、PDF 下载、判题账号拦截、人才/工时导出源接口。
- `run_full_regression_suite.py`
  - 总入口。
  - 串起 seed、API 回归、批量合同、权限、验证码、下载、浏览器 E2E。

## 推荐用法

先准备手工联调数据：

```bash
cd hr-server
uv run python -m src.scripts.v2.seed_manual_review_data
```

再跑 API 回归：

```bash
cd hr-server
uv run python -m src.scripts.v2.run_api_regression_suite
```

如果本地数据已经准备好了，只想复跑 API：

```bash
cd hr-server
uv run python -m src.scripts.v2.run_api_regression_suite --skip-seed
```

如果还要把高级筛选的大批量校验一起带上：

```bash
cd hr-server
uv run python -m src.scripts.v2.run_api_regression_suite --include-advanced-filter-bulk
```

批量合同链路单独压测：

```bash
cd hr-server
uv run python -m src.scripts.v2.run_batch_contract_mutation_suite
```

前端页面 smoke：

```bash
cd hr-server
uv run python -m src.scripts.v2.run_browser_smoke_suite
```

如果本地已经安装 Playwright，并且要强制真实浏览器：

```bash
cd hr-server
uv run python -m src.scripts.v2.run_browser_smoke_suite --require-browser
```

真实浏览器 E2E：

```bash
cd hr-server
uv run python -m src.scripts.v2.run_browser_e2e_suite
```

权限矩阵、验证码、下载导出专项：

```bash
cd hr-server
uv run python -m src.scripts.v2.run_permission_matrix_suite
uv run python -m src.scripts.v2.run_register_verification_suite
uv run python -m src.scripts.v2.run_export_download_suite
```

一键完整回归：

```bash
cd hr-server
uv run python -m src.scripts.v2.run_full_regression_suite
```

## 默认覆盖的主链路

- Public Jobs 搜索与岗位详情
- Candidate My Jobs / My Contracts 列表与详情
- Candidate 合同附件下载
- Assessment 上传格式边界
- Signed Contract 上传格式边界
- Inactive Contract 上传封禁
- Candidate Working Hours / Referral / Earnings
- Admin 合同库 / 人才库查询
- Admin Referral / Payment Record 过滤校验
- Admin Timesheet Overview / Workspace
- Admin Progress 基础探活
- Admin Payment Record 批量新增 + 非法 Referral Reward 拦截
- Admin Payables 结算/支付 + Payment 不可变流水 / Candidate Referral 联动
- Admin Contract 编辑 + 非法激活拦截 + 重签
- Admin Timesheet 批量新增 / 编辑 / 删除 + 整数边界校验
- Progress 合同链路：补合同记录 -> 上传待签合同 -> C 端签回 -> 审核通过 -> 上传公司签回合同 -> 激活
- Batch Contract 合同链路：多候选人合同编号匹配 -> 批量待签上传 -> 批量通知签约 -> 多候选人回传 -> 批量审核通过 -> 公司签回上传 -> 全部入职
- Browser Smoke：Admin / C 端核心页面可访问，登录态 API 可用；有 Playwright 时检查页面渲染和控制台错误
- Browser E2E：真实 Chromium 渲染、未登录跳转、Admin/C 端核心页面、CSV 下载按钮、C 端横向滚动守卫
- Permission Matrix：超管设置权限、普通账号业务权限、判题账号隔离、匿名和 C 端 token 拦截
- Register Verification：无验证码失败、错误验证码计数、正确验证码注册、验证码消费、重复注册拦截
- Export/Download：候选人本人附件下载、跨用户拦截、Admin 下载/PDF 转换、判题账号资产隔离、导出源数据可用

## 产物位置

所有日志和汇总报告默认写到：

```text
hr-server/tmp/v2/
```

## 说明

- `seed_manual_review_data.py` 适合长期保留，给你手工点页面。
- `run_api_regression_suite.py` 更偏临时回归，后续如果链路继续变化，可以删掉重写，不必背兼容包袱。
