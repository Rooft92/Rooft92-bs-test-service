"""Настройки из переменных окружения (.env подхватывает docker compose). Секреты в коде не храним."""
import os
from dataclasses import dataclass


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    app_name: str
    log_level: str
    db_path: str
    git_sha: str
    b24_webhook_url: str
    b24_app_token: str
    orders_url: str


settings = Settings(
    app_name=env("APP_NAME", "bs-test-service"),
    log_level=env("LOG_LEVEL", "INFO").upper(),
    db_path=env("DB_PATH", "data/app.db"),
    git_sha=env("GIT_SHA", "dev"),
    # Входящий вебхук Битрикс24: https://<портал>.bitrix24.ru/rest/<user>/<токен>/
    b24_webhook_url=env("B24_WEBHOOK_URL"),
    # application_token исходящего вебхука — им проверяем, что событие пришло от нашего портала
    b24_app_token=env("B24_APP_TOKEN"),
    # Источник «сырых заявок» для мини-синхронизатора (/result)
    orders_url=env("ORDERS_URL", "https://test.ooobs.ru/api/orders"),
)
