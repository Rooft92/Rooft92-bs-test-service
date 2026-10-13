# bs-test-service

Небольшой сервис интеграций на FastAPI. Он принимает вебхуки, в том числе события Битрикс24, и не создаёт дублей. Ошибки пишутся в журнал, упавшее событие можно обработать повторно. В комплекте клиент REST API Битрикс24 с повтором при сбоях и лимитах, batch-запросами и постраничной выборкой.

## Эндпоинты

| Метод | Путь | Что делает |
|---|---|---|
| POST | `/webhook`, `/webhook/{source}` | Приём события (JSON или form-urlencoded). Дубль определяется по заголовку `Idempotency-Key`, а без него — по хешу тела |
| POST | `/webhook/b24` | События Битрикс24. Проверяется `auth[application_token]`, если задан `B24_APP_TOKEN` |
| GET | `/events?status=failed` | Последние события |
| GET | `/events/{id}` | Одно событие целиком |
| POST | `/events/{id}/retry` | Повторная обработка |
| GET | `/errors` | Журнал ошибок |
| GET | `/health` | Статус и версия (коммит) |
| GET | `/docs` | Swagger |

## Как устроено

- **Без дублей.** У каждого события есть уникальный ключ в SQLite (`INSERT OR IGNORE`). Повторный вебхук возвращает id уже принятого события и не обрабатывается второй раз.
- **Повтор при сбое.** Клиент Битрикс24 повторяет запрос с растущей паузой при сетевых ошибках, ответах 5xx/429 и `QUERY_LIMIT_EXCEEDED`. Если обработка события упала, оно получает статус `failed` и перезапускается через `/events/{id}/retry`.
- **Лог ошибок.** Ошибки пишутся в stdout (`docker compose logs`) и в таблицу `errors`, которую показывает `/errors`.
- **Секреты.** Хранятся только в переменных окружения (`.env`, в git не попадает). Токены из `auth` входящих вебхуков не сохраняются.

## Локальный запуск

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest
.venv/bin/uvicorn app.main:app --reload
```

## Выкладка (Docker)

```bash
cp .env.example .env          # заполнить
docker compose up -d --build
```

- **HTTPS.** В `.env` укажите `COMPOSE_PROFILES=https` и `DOMAIN=<ip через дефисы>.sslip.io`. Caddy сам получит сертификат.
- **Автовыкладка при push.** Запустите `tmux new -s bsdeploy 'bash deploy/autodeploy.sh'`.
- **Удаление.** `docker compose --profile https down -v --rmi local`, затем удалите папку проекта.
