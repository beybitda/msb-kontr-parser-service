from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import get_settings
from app.db.connection import close_pool, init_pool

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool()
    logger.info("Service started")
    yield
    close_pool()
    logger.info("Service stopped")


app = FastAPI(
    title="MSB Kontr Parser Service",
    description="Парсинг статусов контрактов (Госзакупки / Самрук-Казына), "
    "триггер — Informatica post-session command. Без внешних оркестраторов (Airflow не используется).",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
