# HR Server 最小开发文档

## 目标

当前仓库已经收敛成一个更轻量的后端基线：

- `web` 服务入口：`src.app.main_web:app`
- `admin` 服务入口：`src.app.main_admin:app`
- 共享业务模块放在 `src/app/modules`
- `admin` 专属 HTTP 接口放在 `src/app/admin/api`
- `web` 专属 HTTP 接口放在 `src/app/api`

数据库当前默认使用 MySQL，缓存使用 Redis。

## 目录约定

### 共享模块

共享模块放在：

```text
src/app/modules/
```

当前主要有：

- `modules/user`
- `modules/admin/admin_user`
- `modules/admin/role`

模块目录只保留这 5 类文件：

- `const.py`
- `model.py`
- `schema.py`
- `crud.py`
- `service.py`

### HTTP 层

客户端相关接口：

```text
src/app/api/
```

后台管理相关接口：

```text
src/app/admin/api/
```

## 环境准备

在项目根目录执行：

```bash
cd /Users/ruanhaokang/workspace/hr/hr-server
uv sync
cp src/.env.example src/.env
```

至少要配置：

```env
ENVIRONMENT="local"

# 本地虚拟管理员免登录；非 local 环境即使误配也会拒绝启动
ENABLE_LOCAL_AUTH_BYPASS=true

# 默认不向数据库写入固定开发管理员
ENABLE_LOCAL_ADMIN_BOOTSTRAP=false

SECRET_KEY="replace-with-a-real-secret"

DATABASE_BACKEND="mysql"
MYSQL_USER="root"
MYSQL_PASSWORD="your-db-password"
MYSQL_SERVER="127.0.0.1"
MYSQL_PORT=3306
MYSQL_DB="hr_server"

REDIS_CACHE_HOST="127.0.0.1"
REDIS_CACHE_PORT=6379
```

`ENABLE_LOCAL_AUTH_BYPASS=true` 时，可以使用用户名 `HaokangImport` 和任意非空密码登录 Admin。它是进程内虚拟超级管理员，不会写入数据库；`/me` 和 refresh 仍可正常使用。关闭该开关后，登录立即回到真实数据库账号校验。

这个虚拟管理员是服务端 refresh session 的唯一例外：为了保持本地无数据库免登录，它的 refresh 仍是仅限 local 的签名 token，并且每次验证都会再次检查 bypass 开关。普通 Web/Admin 账号全部使用数据库中的单次轮换 refresh session。

如果确实需要数据库中的固定本地管理员，可以仅在 `ENVIRONMENT=local` 时显式设置 `ENABLE_LOCAL_ADMIN_BOOTSTRAP=true`。这个开关与虚拟免登录相互独立。

真实普通管理员不再继承隐式默认权限：角色勾选哪些权限，登录后就只有哪些权限；没有角色的账号只能完成认证，访问受保护的后台业务接口会得到 403。本地虚拟 `HaokangImport` 仍是显式超级管理员例外。

本地需要创建或发送邮件账号时，还要生成一个 Fernet key：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

将输出写入 `src/.env` 的 `MAIL_CREDENTIAL_ENCRYPTION_KEY`。邮件账号的 SMTP 授权码是只写字段：创建/更新时提交，查询时只会看到 `has_auth_secret=true|false`。

本地资产默认写入 `ASSET_STORAGE_DIR`。前端拿到的始终是需要登录态的预览/下载 API，不是底层文件路径；默认单文件上限为 25 MiB，批量 ZIP 最多 50 个文件、未压缩内容总计 100 MiB，这些值都可以在 `src/.env` 中调整。

仓库当前的 Ruff 门禁覆盖 `src` 和 `tests` 全部文件。mypy 门禁先覆盖安全配置、日志/过滤器、事件队列、refresh session、outbox、角色权限和资产模块，共 42 个生产源文件；其他历史业务模块仍有已知类型债务，因此 CI 明确命名为 `Core Type Checking`，不会把局部通过描述成全仓通过。

## 数据库初始化

当前项目不会自动创建数据库本身。

你需要先手动创建 MySQL 数据库，例如：

```sql
CREATE DATABASE IF NOT EXISTS hr_server
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;
```

然后再执行 Alembic 建表：

```bash
cd src
uv run alembic upgrade head
cd ..
```

## 本地启动

### Web

```bash
uv run python run_web.py
```

默认地址：

- `http://127.0.0.1:8000`

### Admin

```bash
uv run python run_admin.py
```

默认地址：

- `http://127.0.0.1:8001`

## Alembic 使用方式

Alembic 是数据库结构版本管理工具。

这里要区分两件事：

- 创建数据库：手动执行 SQL
- 创建或修改表结构：Alembic 管理

你以后如果新增表、加字段、改字段，推荐流程是：

1. 先改 SQLAlchemy 模型
2. 生成迁移文件
3. 检查迁移内容
4. 执行升级

### 生成迁移

```bash
cd src
uv run alembic revision --autogenerate -m "describe your change"
```

### 执行迁移

```bash
uv run alembic upgrade head
```

### 回退一步

```bash
uv run alembic downgrade -1
```

## 首个管理员

数据库建好并迁移后，再创建首个管理员：

```bash
cd /Users/ruanhaokang/workspace/hr/hr-server
uv run python -m src.scripts.create_first_superuser
```

脚本会交互式要求输入：

- `Name`
- `Email`
- `Username`
- `Password`

如果你只是本地快速初始化，也可以直接创建默认管理员：

```bash
cd /Users/ruanhaokang/workspace/hr/hr-server
uv run python -m src.scripts.create_first_superuser --default
```

默认账号是：

- `admin@admin.com`
- `12345678`

## 当前主要接口

### Web

- `POST /api/v1/login`
- `POST /api/v1/refresh`
- `POST /api/v1/user/register`
- `GET /api/v1/user/me`
- `PATCH /api/v1/user/me`
- `DELETE /api/v1/user/me`

### Admin

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/logout`
- `GET /api/v1/accounts`
- `POST /api/v1/accounts`
- `GET /api/v1/accounts/{account_id}`
- `PATCH /api/v1/accounts/{account_id}`
- `DELETE /api/v1/accounts/{account_id}`
- `GET /api/v1/permissions/catalog`
- `GET /api/v1/roles`
- `POST /api/v1/roles`
- `GET /api/v1/roles/{role_id}`
- `PATCH /api/v1/roles/{role_id}`
- `DELETE /api/v1/roles/{role_id}`

## 当前数据库设计原则

- 核心稳定字段优先使用显式列
- `data` 字段先保留，作为扩展字段预留
- 不再默认把核心字段都做成 JSON 虚拟列

## 当前建议

- 不要再修改已经执行过的旧迁移文件
- 真正进入多人协作后，每次表结构变更都新建 Alembic revision
- 先继续补业务，再逐步补测试
