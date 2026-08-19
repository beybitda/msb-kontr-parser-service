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
    chromium_executable_path: str = "/usr/bin/chromium"
    request_timeout_sec: float = 60.0
    request_delay_min_sec: float = 0.5
    request_delay_max_sec: float = 2.0
    max_attempts: int = 5

    goszakup_base_url: str = "https://goszakup.gov.kz/ru/registry/contract"
    goszakup_detail_url_template: str = "https://goszakup.gov.kz/ru/egzcontract/cpublic/show/{id}"
    goszakup_search_count_record: int = 50
    
    samruk_base_url: str = "https://zakup.sk.kz/#/ext?tabs=contractCard&page=1"

    # --- Логирование ---
    log_level: str = "INFO"
    # путь до лог-файла; директория создаётся автоматически, если её нет.
    # относительный путь -> относительно рабочей директории процесса
    # (в контейнере это /app, см. Dockerfile/docker-compose.yml).
    log_file: str = "logs/app.log"
    log_to_console: bool = False  # дублировать ли лог в stdout/терминал
    log_max_bytes: int = 10 * 1024 * 1024  # 10 MB на файл до ротации
    log_backup_count: int = 5  # сколько ротированных файлов хранить

    # --- Логическое имя сквозного процесса (общий PROCESS_NAME с Informatica) ---
    process_name: str = "GRN_BLANK_MONITORING"


@lru_cache
def get_settings() -> Settings:
    return Settings()
