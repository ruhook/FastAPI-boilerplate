import argparse
import shutil
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from ..app.core.config import settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset local database and asset storage to an empty state.")
    parser.add_argument(
        "--keep-assets",
        action="store_true",
        help="Keep files under storage/assets and only recreate the database.",
    )
    return parser.parse_args()


def reset_mysql_database() -> None:
    db_url = make_url(settings.MYSQL_SYNC_URL)
    database_name = db_url.database
    if not database_name:
        raise RuntimeError("MYSQL database name is empty.")

    server_url = db_url.set(database=None)
    engine = create_engine(server_url, future=True)
    quoted_name = database_name.replace("`", "``")

    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP DATABASE IF EXISTS `{quoted_name}`")
        connection.exec_driver_sql(
            f"CREATE DATABASE `{quoted_name}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )

    engine.dispose()


def clear_asset_storage() -> None:
    root = Path(settings.ASSET_STORAGE_DIR)
    if not root.exists():
        return
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def main() -> None:
    args = parse_args()
    backend = settings.DATABASE_BACKEND.lower()
    if backend != "mysql":
        raise RuntimeError(f"Unsupported backend for reset_local_state.py: {backend}")

    print(f"[reset] recreating database `{settings.MYSQL_DB}`")
    reset_mysql_database()

    if args.keep_assets:
        print("[reset] keeping asset storage")
    else:
        print(f"[reset] clearing asset storage `{settings.ASSET_STORAGE_DIR}`")
        clear_asset_storage()

    print("[reset] local state reset complete")


if __name__ == "__main__":
    main()
