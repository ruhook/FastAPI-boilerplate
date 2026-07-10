# HR Server 部署说明

## 目标

本项目目前分为两个服务入口：

- `web`: `src.app.main_web:app`
- `admin`: `src.app.main_admin:app`

开发环境推荐使用 `uv` 管理依赖并通过 `uv run python` 启动。  
生产环境推荐使用 `gunicorn` 启动，并使用 `supervisor` 托管进程。

## 1. 服务器准备

建议准备：

- Python 3.11 或 3.12
- MySQL
- Redis
- `supervisor`
- 一个独立虚拟环境

示例目录：

```bash
/srv/hr-server
```

## 2. 拉代码

```bash
cd /srv
git clone git@github.com:YOUR_ORG/YOUR_REPO.git hr-server
cd hr-server
```

## 3. 安装依赖

如果服务器也安装了 `uv`：

```bash
uv sync
```

如果你更习惯虚拟环境，也可以：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

重点是最终需要能直接执行：

```bash
gunicorn
```

## 4. 配置环境变量

项目读取 `src/.env`。这个文件不应该提交到 Git。

先复制模板：

```bash
cp src/.env.example src/.env
```

然后修改至少这些值：

```env
ENVIRONMENT="production"

SECRET_KEY="replace-with-a-real-secret"

CORS_ORIGINS=["https://admin.example.com","https://app.example.com"]
CORS_ALLOW_CREDENTIALS=true
ENABLE_LOCAL_AUTH_BYPASS=false
ENABLE_LOCAL_ADMIN_BOOTSTRAP=false

MAIL_CREDENTIAL_ENCRYPTION_KEY="replace-with-a-generated-fernet-key"

DATABASE_BACKEND="mysql"

MYSQL_USER="hr_user"
MYSQL_PASSWORD="your-db-password"
MYSQL_SERVER="127.0.0.1"
MYSQL_PORT=3306
MYSQL_DB="hr_server"

REDIS_CACHE_HOST="127.0.0.1"
REDIS_CACHE_PORT=6379

```

生产环境约束：

- `SECRET_KEY` 必须是至少 32 个字符的非示例值；
- 携带 Cookie/Authorization 凭据时，`CORS_ORIGINS` 必须列出真实前端域名，不能使用 `*`；
- 两个本地开发开关在 staging/production 都必须为 `false`，否则服务拒绝启动；
- `MAIL_CREDENTIAL_ENCRYPTION_KEY` 必须是 Fernet key，可用下面的命令生成，并保存到部署密钥管理系统：

```bash
# 生成 SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"

# 生成 MAIL_CREDENTIAL_ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

这个 key 用于解密数据库中的 SMTP 凭据，不能在普通发布中随意更换。邮件账号的查询接口只返回 `has_auth_secret`，不会再返回 SMTP 授权码。

仓库历史中曾出现过候选人注册邮件的 SMTP 授权码。上线前必须在邮件服务商后台撤销并重新生成该授权码，再通过 `CANDIDATE_REGISTER_VERIFICATION_AUTH_SECRET` 注入；只从当前代码删除旧值不等于完成轮换。

首次管理员账号不再从 `.env` 读取，而是通过脚本运行时交互输入。

## 5. 初始化数据库

当前项目不会自动创建数据库本身。

你需要先在 MySQL 里手动建库，例如：

```sql
CREATE DATABASE IF NOT EXISTS hr_server
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;
```

然后再执行 Alembic 迁移建表。

## 6. 执行数据库迁移

```bash
cd /srv/hr-server/src
uv run alembic upgrade head
```

如果你不是用 `uv`，就在已激活虚拟环境里执行：

```bash
alembic upgrade head
```

## 7. 创建首个管理员

执行：

```bash
cd /srv/hr-server
uv run python -m src.scripts.create_first_superuser
```

脚本会交互式提示输入：

- `Name`
- `Email`
- `Username`
- `Password`

## 8. 本地开发启动方式

web:

```bash
uv run python run_web.py
```

admin:

```bash
uv run python run_admin.py
```

默认端口：

- web: `8000`
- admin: `8001`

## 9. 生产启动方式

### web

```bash
gunicorn -c gunicorn_web.conf.py src.app.main_web:app
```

### admin

```bash
gunicorn -c gunicorn_admin.conf.py src.app.main_admin:app
```

如果需要自定义绑定地址或 worker 数，可以通过环境变量：

```bash
WEB_GUNICORN_BIND=0.0.0.0:8000 WEB_GUNICORN_WORKERS=4 gunicorn -c gunicorn_web.conf.py src.app.main_web:app
ADMIN_GUNICORN_BIND=0.0.0.0:8001 ADMIN_GUNICORN_WORKERS=2 gunicorn -c gunicorn_admin.conf.py src.app.main_admin:app
```

## 10. 使用 Supervisor 托管

仓库里提供了示例文件：

- `deploy/supervisor/hr-server.conf.example`

你可以复制到 supervisor 配置目录：

```bash
sudo cp deploy/supervisor/hr-server.conf.example /etc/supervisor/conf.d/hr-server.conf
```

然后把里面这些路径改成你服务器上的真实值：

- `/path/to/hr-server`
- `/path/to/venv/bin/gunicorn`
- `user=www`
- 日志文件位置

更新配置并启动：

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status
```

重启：

```bash
sudo supervisorctl restart hr-web
sudo supervisorctl restart hr-admin
```

## 11. 推荐发布流程

首次发布 SMTP 加密改造时，按以下顺序执行，使新旧明文数据在兼容窗口内平滑切换：

```bash
cd /srv/hr-server
git pull
uv sync

# 先配置并持久化 MAIL_CREDENTIAL_ENCRYPTION_KEY，再升级表结构
cd src && uv run alembic upgrade head
cd ..

# 先让能够同时读取新密文和旧明文的新版本生效
sudo supervisorctl restart hr-web
sudo supervisorctl restart hr-admin

# 将存量 SMTP 明文加密并清空旧列；输出仅包含 migrated/skipped 数量
uv run python -m src.scripts.encrypt_mail_account_credentials
```

如果存在独立事件/邮件 worker，也必须先升级并重启到新版本，再运行加密命令。迁移命令可以重跑：已经有密文或没有旧明文的记录会跳过。执行后请验证发信链路，并妥善备份 `MAIL_CREDENTIAL_ENCRYPTION_KEY`；丢失该 key 时，现有 SMTP 凭据无法恢复，只能重新录入。

## 12. 建议

- `web` 和 `admin` 建议走不同域名或至少不同子路径/端口
- 生产环境请务必替换 `SECRET_KEY`、数据库密码和所有第三方授权码
- 不要在生产环境开启 `ENABLE_LOCAL_AUTH_BYPASS` 或 `ENABLE_LOCAL_ADMIN_BOOTSTRAP`
- 不要把 `MAIL_CREDENTIAL_ENCRYPTION_KEY` 写进 Git、日志或工单正文
- `src/.env` 不要提交到 Git
- 如果后续接 Nginx，建议由 Nginx 统一代理到 `8000/8001`
