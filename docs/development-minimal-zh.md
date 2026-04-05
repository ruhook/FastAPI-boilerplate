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
