from fastapi import Header, HTTPException, status

from app.core.config import get_settings


async def verify_api_key(x_api_key: str = Header(default="")) -> None:
    """Простая проверка статического ключа, которым Informatica подписывает
    post-session command при вызове /parser/trigger.

    Заголовок: X-API-Key: <PARSER_API_KEY>
    """
    settings = get_settings()
    if not x_api_key or x_api_key != settings.parser_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-API-Key",
        )
