# Server Script Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除七个确定遗留或重复的 Server 脚本，修复 V2 演示申请的国家字典契约，并把测试手册收敛到 V2 主入口。

**Architecture:** 保留完整 Alembic 迁移链、正式运维入口和 V2 所需共享 seed。以两个纯函数回归测试锁定 `nationality`/`country_of_residence` 必须来自全局国家字典，再删除无持续职责的脚本并同步唯一的测试入口文档。

**Tech Stack:** Python 3.11+、FastAPI、pytest、Ruff、mypy、Alembic、Git

## Global Constraints

- 不改变生产 API、数据库结构或线上启动流程。
- 保留全部 `src/migrations/versions/*.py`，当前迁移 head 必须仍为 `20260710_000047`。
- 保留 `create_first_superuser.py`、`reset_local_state.py` 和 V2 当前依赖的共享 seed/helper。
- 新增回归测试必须先因 `Brazilian` 失败，再以最小生产代码修改变绿。
- 实时 V2 full regression 只有在所需本地服务已启动时才执行；不得以静态检查冒充端到端通过。

---

### Task 1: 用 TDD 修复演示申请国家字典契约

**Files:**
- Create: `tests/scripts/test_demo_application_dictionary_values.py`
- Modify: `src/scripts/run_client_apply_demo.py:225-268`
- Modify: `src/scripts/seed_job_progress_demo_flow.py:128-188`

**Interfaces:**
- Consumes: `CandidateFieldKey`, `GLOBAL_COUNTRY_OPTIONS`, `run_client_apply_demo.build_application_items(...)`, `seed_job_progress_demo_flow.build_application_items(...)`。
- Produces: 两个 builder 均为 `nationality` 和 `country_of_residence` 返回合法全局国家字典值 `Brazil`。

- [ ] **Step 1: 写入失败的字典契约测试**

```python
from src.app.modules.candidate_field.const import CandidateFieldKey
from src.app.modules.candidate_field.global_dictionary_options import GLOBAL_COUNTRY_OPTIONS
from src.scripts import run_client_apply_demo, seed_job_progress_demo_flow


COUNTRY_VALUES = {str(option["value"]) for option in GLOBAL_COUNTRY_OPTIONS}


def _assert_country_fields_are_dictionary_values(items: list[dict[str, object]]) -> None:
    values = {str(item["field_key"]): item["value"] for item in items}

    for field_key in (
        CandidateFieldKey.NATIONALITY.value,
        CandidateFieldKey.COUNTRY_OF_RESIDENCE.value,
    ):
        assert values[field_key] == "Brazil"
        assert values[field_key] in COUNTRY_VALUES


def test_client_apply_demo_uses_country_dictionary_values() -> None:
    items = run_client_apply_demo.build_application_items(
        job_index=0,
        candidate_name="Demo Candidate",
        email="demo@example.com",
        resume_asset_id=1,
    )

    _assert_country_fields_are_dictionary_values(items)


def test_job_progress_demo_uses_country_dictionary_values() -> None:
    items = seed_job_progress_demo_flow.build_application_items(
        scenario_key="assessment_auto_pass",
        candidate_name="Progress Candidate",
        candidate_email="progress@example.com",
        resume_asset_id=2,
    )

    _assert_country_fields_are_dictionary_values(items)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m pytest -q tests/scripts/test_demo_application_dictionary_values.py`

Expected: 两个测试都因实际值为 `Brazilian`、期望值为 `Brazil` 而失败；测试必须是 assertion failure，不是导入或配置错误。

- [ ] **Step 3: 作最小生产代码修复**

在两个 builder 中将国籍默认值改成全局国家字典实际值：

```python
CandidateFieldKey.NATIONALITY.value: "Brazil"
```

以及：

```python
nationality = "Brazil"
```

不得在本任务中改动其他演示数据或 API 校验逻辑。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `.venv/bin/python -m pytest -q tests/scripts/test_demo_application_dictionary_values.py`

Expected: `2 passed`。

- [ ] **Step 5: 运行相关脚本测试**

Run: `.venv/bin/python -m pytest -q tests/scripts tests/modules/test_candidate_english_proficiency_config.py`

Expected: 全部通过，且没有数据库连接或服务依赖错误。

- [ ] **Step 6: 提交契约修复**

```bash
git add tests/scripts/test_demo_application_dictionary_values.py \
  src/scripts/run_client_apply_demo.py \
  src/scripts/seed_job_progress_demo_flow.py
git commit -m "fix: align demo applications with country dictionary"
```

### Task 2: 删除遗留脚本并收敛测试文档

**Files:**
- Delete: `scripts/local_admin_bootstrap.test.py`
- Delete: `src/scripts/backfill_referral_profiles.py`
- Delete: `src/scripts/import_haokang_data.py`
- Delete: `src/scripts/import_haokang_visible_payload.py`
- Delete: `src/scripts/seed_ruanhaokang_candidate_jobs.py`
- Delete: `src/scripts/run_recruitment_e2e_demo.py`
- Delete: `src/scripts/run_client_signed_contract_upload_demo.py`
- Modify: `docs/testing-playbook-zh.md:38-154`

**Interfaces:**
- Consumes: `src.scripts.v2.seed_manual_review_data` 和 `src.scripts.v2.run_full_regression_suite` 已有 CLI。
- Produces: 测试手册只推荐仍被维护的 V2 综合入口，并仅保留有效的 Candidate 报名/测试题上传单点脚本。

- [ ] **Step 1: 删除七个目标脚本**

应用以下删除补丁；不删除任何 Alembic migration、V2 模块或仍被测试导入的 helper：

```text
*** Begin Patch
*** Delete File: scripts/local_admin_bootstrap.test.py
*** Delete File: src/scripts/backfill_referral_profiles.py
*** Delete File: src/scripts/import_haokang_data.py
*** Delete File: src/scripts/import_haokang_visible_payload.py
*** Delete File: src/scripts/seed_ruanhaokang_candidate_jobs.py
*** Delete File: src/scripts/run_recruitment_e2e_demo.py
*** Delete File: src/scripts/run_client_signed_contract_upload_demo.py
*** End Patch
```

- [ ] **Step 2: 用统一手工 seed 替换旧 B/C 端数据准备说明**

将原测试手册第 3、4 节替换为：

````markdown
## 3. 准备统一手工验收数据

```bash
cd /Users/ruanhaokang/workspace/hr/hr-server
uv run python -m src.scripts.v2.seed_manual_review_data
```

该入口会统一准备招聘流程、判题账号、合同、工时、收益、邀请奖励和 Candidate Portal 页面数据，并在 `tmp/v2/` 生成账号、页面路径和子脚本日志汇总。

## 4. 完整回归

```bash
cd /Users/ruanhaokang/workspace/hr/hr-server
uv run python -m src.scripts.v2.run_full_regression_suite
```

如果当前没有可用的 Chromium 环境，可加 `--skip-browser`；各专项回归入口见 `src/scripts/v2/README.md`。
````

保留人工检查页面说明，但账号以 `manual-review-seed-v2-*.json` 实际输出为准，不复制第二套易漂移默认值。

- [ ] **Step 3: 删除已移除签回合同单点脚本说明**

在“单点脚本”中只保留下列两个命令：

```bash
uv run python -m src.scripts.run_client_apply_demo
uv run python -m src.scripts.run_client_assessment_upload_demo
```

签回合同由 V2 API regression 和 batch contract suite 覆盖，不再保留旧命令。

- [ ] **Step 4: 扫描活跃引用**

Run:

```bash
rg -n "local_admin_bootstrap\.test|backfill_referral_profiles|import_haokang_data|import_haokang_visible_payload|seed_ruanhaokang_candidate_jobs|run_recruitment_e2e_demo|run_client_signed_contract_upload_demo" README.md docs/testing-playbook-zh.md .github src tests scripts 2>/dev/null
```

Expected: 无匹配。历史设计/实施文档可保留删除决策记录，不属于活跃入口。

- [ ] **Step 5: 运行脚本范围静态检查和测试**

Run: `uv run ruff check --no-fix src/scripts tests/scripts`

Expected: `All checks passed!`。

Run: `.venv/bin/python -m pytest -q tests/scripts tests/core/test_security_config.py tests/modules/test_candidate_english_proficiency_config.py`

Expected: 全部通过。

- [ ] **Step 6: 提交脚本清理**

```bash
git add -A -- \
  scripts/local_admin_bootstrap.test.py \
  src/scripts/backfill_referral_profiles.py \
  src/scripts/import_haokang_data.py \
  src/scripts/import_haokang_visible_payload.py \
  src/scripts/seed_ruanhaokang_candidate_jobs.py \
  src/scripts/run_recruitment_e2e_demo.py \
  src/scripts/run_client_signed_contract_upload_demo.py \
  docs/testing-playbook-zh.md
git commit -m "chore: remove obsolete server scripts"
```

### Task 3: 完整验证清理结果

**Files:**
- Verify: `src/migrations/versions/*.py`
- Verify: `.github/workflows/tests.yml`
- Verify: `.github/workflows/linting.yml`
- Verify: `.github/workflows/type-checking.yml`

**Interfaces:**
- Consumes: Task 1 的国家字典契约测试和 Task 2 的精简脚本树。
- Produces: 与 CI 对齐的测试、lint、类型检查、迁移 head 和引用扫描证据。

- [ ] **Step 1: 检查补丁完整性**

Run: `git status --short && git diff --check HEAD~2..HEAD`

Expected: 工作树干净；`git diff --check` 无输出且退出 0。

- [ ] **Step 2: 运行完整 pytest**

Run: `env ALLOW_TEST_DATABASE_CLEANUP=true uv run python -m pytest -q`

Expected: 全部测试通过。测试数据库安全守卫必须确认当前数据库在 allowlist 中；如果本机 MySQL/Redis 未运行或测试数据库配置不安全，停止该命令并如实记录环境阻塞。

- [ ] **Step 3: 运行 CI Ruff 门**

Run: `uv run --frozen ruff check --no-fix src tests`

Expected: `All checks passed!`。

- [ ] **Step 4: 运行 CI mypy 门**

Run:

```bash
env ENVIRONMENT=local SECRET_KEY=test-secret-key-for-testing-only \
  uv run --frozen mypy \
  src/app/core \
  src/app/event \
  src/app/modules/auth_refresh_session \
  src/app/modules/event_outbox \
  src/app/modules/admin/mail_account \
  src/app/modules/admin/mail_task \
  src/app/modules/admin/role \
  src/app/modules/assets \
  --config-file pyproject.toml
```

Expected: `Success: no issues found`。

- [ ] **Step 5: 验证迁移链 head**

Run: `cd src && ../.venv/bin/alembic -c alembic.ini heads`

Expected: `20260710_000047 (head)`。

- [ ] **Step 6: 最终引用和规模检查**

Run:

```bash
rg -n "local_admin_bootstrap\.test|backfill_referral_profiles|import_haokang_data|import_haokang_visible_payload|seed_ruanhaokang_candidate_jobs|run_recruitment_e2e_demo|run_client_signed_contract_upload_demo" README.md docs/testing-playbook-zh.md .github src tests scripts 2>/dev/null
```

Expected: 无匹配。

Run: `git ls-files 'src/scripts/*.py' 'src/scripts/v2/*.py' 'scripts/*.py' | rg -v '(__init__\.py)$' | wc -l`

Expected: 可执行脚本数量由 30 降为 23。

- [ ] **Step 7: 确认工作树和提交记录**

Run: `git status --short && git log --oneline -4`

Expected: 工作树干净，最近提交包含设计、契约修复和脚本清理。
