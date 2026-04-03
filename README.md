<h1 align="center"> Benav Labs FastAPI boilerplate</h1>
<p align="center" markdown=1>
  <i><b>Batteries-included FastAPI starter</b> with production-ready defaults, optional modules, and clear docs.</i>
</p>

<p align="center">
  <a href="https://benavlabs.github.io/FastAPI-boilerplate">
    <img src="docs/assets/FastAPI-boilerplate.png" alt="Purple Rocket with FastAPI Logo as its window." width="25%" height="auto">
  </a>
</p>

<p align="center">
📚 <a href="https://benavlabs.github.io/FastAPI-boilerplate/">Docs</a> · 🧠 <a href="https://deepwiki.com/benavlabs/FastAPI-boilerplate">DeepWiki</a> · 💬 <a href="https://discord.com/invite/TEmPs22gqB">Discord</a>
</p>

<p align="center">
  <a href="https://fastapi.tiangolo.com">
      <img src="https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi" alt="FastAPI">
  </a>
  <a href="https://www.postgresql.org">
      <img src="https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
  </a>
  <a href="https://redis.io">
      <img src="https://img.shields.io/badge/Redis-DC382D?logo=redis&logoColor=fff&style=for-the-badge" alt="Redis">
  </a>
  <a href="https://deepwiki.com/benavlabs/FastAPI-boilerplate">
      <img src="https://img.shields.io/badge/DeepWiki-1F2937?style=for-the-badge&logoColor=white" alt="DeepWiki">
  </a>
</p>

## Features

* ⚡️ Fully async FastAPI + SQLAlchemy 2.0
* 🧱 Pydantic v2 models & validation
* 🔐 JWT auth (access + refresh), cookies for refresh
* 🧰 FastCRUD for efficient CRUD & pagination
* 🧊 Redis caching (server + client-side headers)
* 🌐 Configurable CORS middleware for frontend integration
* 🐳 One-command Docker Compose
* 🚀 NGINX & Gunicorn recipes for prod

## Why and When to use it

**Perfect if you want:**

* A pragmatic starter with auth, CRUD, caching, and a split web/admin structure
* **Sensible defaults** with the freedom to opt-out of modules
* **Docs over boilerplate** in README - depth lives in the site

> **Not a fit** if you need a monorepo microservices scaffold - [see the docs](https://benavlabs.github.io/FastAPI-boilerplate/user-guide/project-structure/) for pointers.

**What you get:**

* **App**: FastAPI app factory, [env-aware docs](https://benavlabs.github.io/FastAPI-boilerplate/user-guide/development/) exposure
* **Auth**: [JWT access/refresh](https://benavlabs.github.io/FastAPI-boilerplate/user-guide/authentication/), logout via token blacklist
* **DB**: MySQL + SQLAlchemy 2.0, [Alembic migrations](https://benavlabs.github.io/FastAPI-boilerplate/user-guide/database/)
* **CRUD**: [FastCRUD generics](https://benavlabs.github.io/FastAPI-boilerplate/user-guide/database/crud/) (get, get_multi, create, update, delete, joins)
* **Caching**: [decorator-based endpoints cache](https://benavlabs.github.io/FastAPI-boilerplate/user-guide/caching/); client cache headers
* **Split API**: separate `web` and `admin` entrypoints sharing common modules

This is what we've been using in production apps. Several applications running in production started from this boilerplate as their foundation - from SaaS platforms to internal tools. It's proven, stable technology that works together reliably. Use this as the foundation for whatever you want to build on top.

> **Building an AI SaaS?** Skip even more setup with [**FastroAI**](https://fastro.ai) - our production-ready template with AI integration, payments, and frontend included.

## TL;DR - Quickstart

Use the template on GitHub, create your repo, then:

```bash
git clone https://github.com/<you>/FastAPI-boilerplate
cd FastAPI-boilerplate
```

**Quick setup:** Run the interactive setup script to choose your deployment configuration:

```bash
./setup.py
```

Or directly specify the deployment type: `./setup.py local`, `./setup.py staging`, or `./setup.py production`.

The script copies the right files for your deployment scenario. Here's what each option sets up:

### Option 1: Local development with Uvicorn

Best for: **Development and testing**

**Copies:**

- `scripts/local_with_uvicorn/Dockerfile` → `Dockerfile`
- `scripts/local_with_uvicorn/docker-compose.yml` → `docker-compose.yml`
- `scripts/local_with_uvicorn/.env.example` → `src/.env`

Sets up Uvicorn with auto-reload enabled. The example environment values work fine for development.

**Manual setup:** `./setup.py local` or copy the files above manually.

### Option 2: Staging with Gunicorn managing Uvicorn workers

Best for: **Staging environments and load testing**

**Copies:**

- `scripts/gunicorn_managing_uvicorn_workers/Dockerfile` → `Dockerfile`
- `scripts/gunicorn_managing_uvicorn_workers/docker-compose.yml` → `docker-compose.yml`
- `scripts/gunicorn_managing_uvicorn_workers/.env.example` → `src/.env`

Sets up Gunicorn managing multiple Uvicorn workers for production-like performance testing.

> [!WARNING]
> Change `SECRET_KEY` and passwords in the `.env` file for staging environments.

**Manual setup:** `./setup.py staging` or copy the files above manually.

### Option 3: Production with NGINX

Best for: **Production deployments**

**Copies:**

- `scripts/production_with_nginx/Dockerfile` → `Dockerfile`
- `scripts/production_with_nginx/docker-compose.yml` → `docker-compose.yml`
- `scripts/production_with_nginx/.env.example` → `src/.env`

Sets up NGINX as reverse proxy with Gunicorn + Uvicorn workers for production.

> [!CAUTION]
> You MUST change `SECRET_KEY`, all passwords, and sensitive values in the `.env` file before deploying!

**Manual setup:** `./setup.py production` or copy the files above manually.

---

**Start your application:**

```bash
docker compose up
```

**Access your app:**
- **Local**: http://127.0.0.1:8000 (auto-reload enabled) → [API docs](http://127.0.0.1:8000/docs)
- **Staging**: http://127.0.0.1:8000 (production-like performance)
- **Production**: http://localhost (NGINX reverse proxy)

### Next steps

**Create your first admin user:**
```bash
docker compose run --rm create_superuser
```

**Run database migrations** (if you add models):
```bash
cd src && uv run alembic revision --autogenerate && uv run alembic upgrade head
```

**Or run locally without Docker:**
```bash
uv sync
cp src/.env.example src/.env
uv run python run_web.py
```

> Full setup examples still live in the upstream docs, but this repo has already been trimmed down to a MySQL + Redis + web/admin split baseline.

## Configuration (minimal)

Create `src/.env` and set **app**, **database**, **JWT**, and **environment** settings. `src/.env` is ignored by git, so you can keep local MySQL credentials, Redis addresses, and secrets there safely. Start from `src/.env.example`.

[https://benavlabs.github.io/FastAPI-boilerplate/getting-started/configuration/](https://benavlabs.github.io/FastAPI-boilerplate/getting-started/configuration/)

* `ENVIRONMENT=local|staging|production` controls API docs exposure
* Create the first admin user explicitly with `uv run python -m src.scripts.create_first_superuser` and follow the interactive prompts

## Common tasks

```bash
# install dependencies
uv sync

# prepare local env file
cp src/.env.example src/.env

# run web app in development
uv run python run_web.py

# run admin app in development
uv run python run_admin.py

# run Alembic migrations
cd src && uv run alembic upgrade head

# run web app with gunicorn
gunicorn -c gunicorn_web.conf.py src.app.main_web:app

# run admin app with gunicorn
gunicorn -c gunicorn_admin.conf.py src.app.main_admin:app

```

More examples for this repo should follow the current `web/admin` split rather than the upstream boilerplate docs.

Minimal local development guide: [docs/development-minimal-zh.md](docs/development-minimal-zh.md)

Production/deploy notes for the split `web/admin` setup: [docs/deployment-zh.md](docs/deployment-zh.md)

## Contributing

Read [contributing](CONTRIBUTING.md).

## References

This project was inspired by a few projects, it's based on them with things changed to the way I like (and pydantic, sqlalchemy updated)

- [`Full Stack FastAPI and PostgreSQL`](https://github.com/tiangolo/full-stack-fastapi-postgresql) by @tiangolo himself
- [`FastAPI Microservices`](https://github.com/Kludex/fastapi-microservices) by @kludex which heavily inspired this boilerplate
- [`Async Web API with FastAPI + SQLAlchemy 2.0`](https://github.com/rhoboro/async-fastapi-sqlalchemy) for sqlalchemy 2.0 ORM examples
- [`FastaAPI Rocket Boilerplate`](https://github.com/asacristani/fastapi-rocket-boilerplate/tree/main) for docker compose

## License

[`MIT`](LICENSE.md)

## Contact

Benav Labs – [benav.io](https://benav.io), [discord server](https://discord.com/invite/TEmPs22gqB)

<hr>
<a href="https://benav.io">
  <img src="https://github.com/benavlabs/fastcrud/raw/main/docs/assets/benav_labs_banner.png" alt="Powered by Benav Labs - benav.io"/>
</a>
