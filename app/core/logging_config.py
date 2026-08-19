from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import get_settings

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging() -> None:
    """Настраивает логирование сервиса: пишем в файл (с ротацией), а не в
    терминал/stdout — сервис запускается в фоне (Docker/systemd), и
    terminal-логи там никто не читает. log_to_console=True в .env
    включает дублирование в stdout (например, для локальной разработки
    без Docker)."""
    settings = get_settings()

    log_path = Path(settings.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            log_path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
    ]
    if settings.log_to_console:
        handlers.append(logging.StreamHandler())

    root = logging.getLogger()
    root.setLevel(settings.log_level)

    # basicConfig не переустанавливает хендлеры, если root уже настроен
    # (например, повторный вызов в тестах) — чистим явно
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(_LOG_FORMAT)
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
