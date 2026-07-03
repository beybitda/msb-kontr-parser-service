from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Oracle ---
    db_user: str
    db_password: str
    db_dsn: str
    db_pool_min: int = 1
    db_pool_max: int = 5
    db_pool_increment: int = 1

    # --- API security ---
    parser_api_key: str

    # --- Парсинг (заготовка на будущее, сейчас парсеры не реализованы) ---
    request_timeout_sec: float = 15.0
    request_delay_min_sec: float = 0.5
    request_delay_max_sec: float = 2.0
    max_attempts: int = 5

    goszakup_base_url: str = "https://goszakup.gov.kz/ru/registry/contract"
    samruk_base_url: str = "https://zakup.sk.kz/#/ext?tabs=contractCard&page=1"

    # --- Прочее ---
    log_level: str = "INFO"

    # --- Логическое имя сквозного процесса (общий PROCESS_NAME с Informatica) ---
    process_name: str = "GRN_BLANK_MONITORING"


@lru_cache
def get_settings() -> Settings:
    return Settings()
