import os

bind = os.getenv("WEB_GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.getenv("WEB_GUNICORN_WORKERS", "2"))
worker_class = "uvicorn.workers.UvicornWorker"
accesslog = "-"
errorlog = "-"
timeout = int(os.getenv("WEB_GUNICORN_TIMEOUT", "60"))
graceful_timeout = int(os.getenv("WEB_GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("WEB_GUNICORN_KEEPALIVE", "5"))
