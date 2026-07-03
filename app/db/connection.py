from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import oracledb

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_pool: oracledb.ConnectionPool | None = None


def init_pool() -> None:
    """Создаёт пул соединений один раз при старте приложения (lifespan)."""
    global _pool
    if _pool is not None:
        return

    settings = get_settings()
    _pool = oracledb.create_pool(
        user=settings.db_user,
        password=settings.db_password,
        dsn=settings.db_dsn,
        min=settings.db_pool_min,
        max=settings.db_pool_max,
        increment=settings.db_pool_increment,
    )
    logger.info("Oracle connection pool initialized: %s", settings.db_dsn)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close(force=True)
        _pool = None
        logger.info("Oracle connection pool closed")


@contextmanager
def get_connection() -> Iterator[oracledb.Connection]:
    if _pool is None:
        # позволяет пользоваться репозиториями и вне жизненного цикла FastAPI
        # (например, в тестах/скриптах), лениво создавая пул
        init_pool()
    assert _pool is not None
    conn = _pool.acquire()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.release(conn)
