# eywa

`eywa` - это сканер транзакций по адресам трейдеров для EVM-сети с сохранением результатов в ClickHouse.

Основной сценарий работы:

1. проект берёт отслеживаемые адреса из таблицы `traders`;
2. ищет связанные транзакции по трейсам блоков (`debug_traceBlockByNumber` + `callTracer`);
3. собирает `receipt`, декодирует swap-события и обогащает их метаданными;
4. сохраняет результат в основные таблицы `transactions`, `swaps`, `tokens`, `liquidity_pools` и `dexes`;
5. после forward scan может выполнить reverse scan по пулам, найти конкурентов и сохранить найденные competitor-адреса в БД.

Reverse scan больше не использует отдельные таблицы: найденные данные смешиваются с основной массой данных в общих таблицах.

## Что есть в проекте

- `app/scripts/scan.py` - standalone forward scan
- `app/scripts/scan_sequence.py` - совместный сценарий: forward scan + reverse scan с настраиваемым источником пулов
- `app/scripts/reverse_scan.py` - standalone reverse scan по пулам и уже известным конкурентам из БД
- `app/scripts/migrate.py` - ожидание ClickHouse, создание БД и применение миграций
- `app/scripts/start.py` - запуск FastAPI-приложения
- `app/main.py` - API с маршрутом `/health`
- `app/core/database.py` - подключение к ClickHouse и запуск Alembic
- `alembic/versions/` - миграции схемы

## Требования

- Python 3.12+
- Docker и Docker Compose для локального ClickHouse
- RPC endpoint EVM-ноды с трейсингом — одним из двух:
  - **`debug` namespace** (`debug_traceBlockByNumber`, `debug_traceTransaction`) — Geth и совместимые. Это режим по умолчанию: `EYWA_TRACE_BACKEND=debug`.
  - **`trace` namespace** (`trace_filter`, `trace_transaction`) — Erigon/OpenEthereum/Nethermind. Включается через `EYWA_TRACE_BACKEND=trace_filter` и работает заметно быстрее, так как нода фильтрует по адресам на своей стороне.

Важно: трейсинг требует состояния (state) на трейсимых блоках. Обычный full node (Geth без `--gcmode=archive`) хранит state только за последние ~128 блоков, поэтому на нём сканируются лишь блоки у головы цепи; для исторических диапазонов нужна archive-нода или archive-провайдер.

`bribe` считается по трейсам транзакции. Если нода не отдаёт нужный метод, проект продолжит работать — `bribe` просто будет `0`.

## Как запускать проект

Есть четыре основных сценария:

- `python -m app.scripts.scan --start-block ... --end-block ...` - запускает только forward scan и пишет результат в основные таблицы
- `python -m app.scripts.scan_sequence --start-block ... --end-block ... --runs ... --pool-source ...` - запускает совместный сценарий `forward -> reverse`
- `python -m app.scripts.reverse_scan --start-block ... --end-block ...` - запускает standalone reverse scan по пулам и уже известным конкурентам из БД
- `python -m app.scripts.start` - применяет миграции и поднимает FastAPI API с `/health`

Важно: `docker compose up --build app` поднимает API-процесс, а не CLI-сканирование.

## Быстрый старт

Рекомендуемый путь: ClickHouse в Docker, сам сканер локально в виртуальном окружении Python.

### 1. Перейти в директорию проекта

```powershell
cd "ПУТЬ_ДО_ПРОЕКТА\eywa"
```

### 2. Создать `.env`

```powershell
Copy-Item .env.example .env
```

Минимально нужно заполнить:

```env
EYWA_RPC_ENDPOINT=https://your-rpc-endpoint

EYWA_CLICKHOUSE_HOST=localhost
EYWA_CLICKHOUSE_PORT=19000
EYWA_CLICKHOUSE_DATABASE=eywa
EYWA_CLICKHOUSE_USERNAME=default
EYWA_CLICKHOUSE_PASSWORD=clickhouse

EYWA_START_BLOCK=24078090
EYWA_CHUNK_SIZE=10000
EYWA_MAX_WORKERS=16
EYWA_TRACE_ADDRESS_BATCH_SIZE=100
```

Для score-фильтрации reverse scan можно дополнительно настроить эвристики в `.env`:

```env
EYWA_REVERSE_HEURISTICS_ENABLED=false
EYWA_REVERSE_HEURISTICS_TOP_LEVEL_FIELD=to
EYWA_REVERSE_HEURISTICS_MIN_PASS_SCORE=4
EYWA_REVERSE_HEURISTICS_TOP_LEVEL_MATCH_MIN=1
EYWA_REVERSE_HEURISTICS_TOP_LEVEL_MATCH_WEIGHT=2
EYWA_REVERSE_HEURISTICS_RAW_TX_MIN=2
EYWA_REVERSE_HEURISTICS_RAW_TX_WEIGHT=2
EYWA_REVERSE_HEURISTICS_POOL_COUNT_MIN=1
EYWA_REVERSE_HEURISTICS_POOL_COUNT_WEIGHT=1
EYWA_REVERSE_HEURISTICS_MATCHED_SWAP_MIN=1
EYWA_REVERSE_HEURISTICS_MATCHED_SWAP_WEIGHT=2
EYWA_REVERSE_HEURISTICS_PRICED_SWAP_MIN=1
EYWA_REVERSE_HEURISTICS_PRICED_SWAP_WEIGHT=1
EYWA_REVERSE_HEURISTICS_TOTAL_USD_MIN=1000
EYWA_REVERSE_HEURISTICS_TOTAL_USD_WEIGHT=2
EYWA_REVERSE_HEURISTICS_MAX_SWAP_USD_MIN=500
EYWA_REVERSE_HEURISTICS_MAX_SWAP_USD_WEIGHT=1
EYWA_REVERSE_HEURISTICS_LOG_SAMPLE_LIMIT=20
```

`EYWA_REVERSE_HEURISTICS_TOP_LEVEL_FIELD=off` полностью отключает top-level сигнал в score.

Если нужен встроенный планировщик в API-процессе, дополнительно настрой:

```env
EYWA_SCHEDULER_ENABLED=true
EYWA_SCHEDULER_CRON=0 * * * *
EYWA_SCHEDULER_TIMEZONE=UTC
EYWA_SCHEDULER_RUNS=1
```

`EYWA_START_BLOCK` используется как fallback только для первого автозапуска планировщика, если в БД ещё нет сохранённых транзакций.
`EYWA_SCHEDULER_RUNS` задаёт число последовательных прогонов `forward -> reverse` в рамках одного cron-срабатывания по одному и тому же зафиксированному диапазону блоков.

### 3. Поднять ClickHouse

```powershell
docker compose up -d clickhouse
```

После старта будут доступны порты:

- HTTP: `18123`
- native protocol: `19000`

### 4. Создать виртуальное окружение и установить зависимости

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

### 5. Применить миграции

```powershell
python -m app.scripts.migrate
```

Эту команду можно пропустить, если запускается `scan.py`, `scan_sequence.py`, `reverse_scan.py` или `start.py`: они вызывают миграции автоматически.

### 6. Добавить стартовые адреса трейдеров в БД

Пример через ClickHouse CLI:

```powershell
docker compose exec clickhouse clickhouse-client --user default --password clickhouse --query "INSERT INTO eywa.traders (contract_address, label) VALUES ('0xabc1230000000000000000000000000000000000', 'seed_trader')"
```

Можно вставить сразу несколько адресов:

```powershell
docker compose exec clickhouse clickhouse-client --user default --password clickhouse --query "INSERT INTO eywa.traders (contract_address, label) VALUES ('0xabc1230000000000000000000000000000000000', 'seed_1'), ('0xdef4560000000000000000000000000000000000', 'seed_2')"
```

### 7. Запустить forward scan

Для запуска нужен явный диапазон блоков:

```powershell
python -m app.scripts.scan --start-block 24078090 --end-block 24078589
```

С явной ролью адреса в forward scan:

```powershell
python -m app.scripts.scan --start-block 24078090 --end-block 24078589 --trace-address-role to
```

На выходе скрипт показывает summary только по forward этапу.

### 8. Запустить совместный scan -> reverse_scan

Для запуска нужен явный диапазон блоков:

```powershell
python -m app.scripts.scan_sequence --start-block 24078090 --end-block 24078589
```

Повторить тот же самый цикл несколько раз на одном и том же диапазоне:

```powershell
python -m app.scripts.scan_sequence --start-block 24078090 --end-block 24078589 --runs 3
```

С явной ролью адреса в forward scan:

```powershell
python -m app.scripts.scan_sequence --start-block 24078090 --end-block 24078589 --trace-address-role to
```

Выбрать источник пулов для reverse-этапа:

```powershell
python -m app.scripts.scan_sequence --start-block 24078090 --end-block 24078589 --pool-source forward
```

Использовать пулы из БД:

```powershell
python -m app.scripts.scan_sequence --start-block 24078090 --end-block 24078589 --pool-source database
```

Использовать объединение обоих источников:

```powershell
python -m app.scripts.scan_sequence --start-block 24078090 --end-block 24078589 --pool-source mixed
```

`--pool-source` поддерживает значения:

- `forward` - пулы только из текущего forward scan
- `database` - пулы только из БД
- `mixed` - объединение пулов из forward scan и БД

Алиас `--reverse-pool-source` тоже поддерживается для совместимости.

По умолчанию `scan_sequence.py` использует `database`.

`--start-block` и `--end-block` обязательны и передаются только вместе.

На выходе скрипт показывает отдельно summary по forward и reverse этапам.

### 9. Запустить standalone reverse scan

Этот режим берёт пулы и уже известных конкурентов прямо из БД и не требует передавать адреса через CLI.

```powershell
python -m app.scripts.reverse_scan --start-block 24078090 --end-block 24078589
```

Чтобы исключить известные адреса конкурентов:

```powershell
python -m app.scripts.reverse_scan --start-block 24078090 --end-block 24078589
```

Standalone reverse scan сохраняет в таблицу `traders` только approved competitor-адреса. В summary отдельно показываются `discovered`, `approved` и `rejected` кандидаты.

## Проверка результата в БД

Последние транзакции:

```powershell
docker compose exec clickhouse clickhouse-client --user default --password clickhouse --query "SELECT hash_id, block_number, trader_address FROM eywa.transactions ORDER BY block_number DESC LIMIT 20"
```

Последние swap-события:

```powershell
docker compose exec clickhouse clickhouse-client --user default --password clickhouse --query "SELECT transaction_hash_id, pool_address, usd_amount, amount_a, amount_b FROM eywa.swaps ORDER BY id DESC LIMIT 20"
```

Содержимое таблицы трейдеров:

```powershell
docker compose exec clickhouse clickhouse-client --user default --password clickhouse --query "SELECT contract_address, label FROM eywa.traders ORDER BY contract_address LIMIT 50"
```

Пулы, появившиеся в базе:

```powershell
docker compose exec clickhouse clickhouse-client --user default --password clickhouse --query "SELECT contract_address, dex_factory, fee_tier FROM eywa.liquidity_pools ORDER BY contract_address LIMIT 50"
```

## Запуск API

Локально:

```powershell
python -m app.scripts.start
```

После старта API будет доступен по адресу:

```text
http://localhost:8000/health
```

Через Docker:

```powershell
docker compose up --build app
```

Если включён scheduler через `EYWA_SCHEDULER_ENABLED=true`, он будет работать внутри API-процесса и запускать совместный сценарий `forward -> reverse`.

Примечание: `app/scripts/start.py` локально всегда поднимает `uvicorn` на порту `8000`. Переменная `EYWA_APP_PORT` влияет на проброс порта в Docker Compose, а не на локальный запуск этого скрипта.

## Полезные команды

Применить миграции вручную:

```powershell
python -m app.scripts.migrate
```

Запустить API без Docker:

```powershell
python -m app.scripts.start
```

Запустить forward scan внутри контейнера `app`:

```powershell
docker compose up -d clickhouse
docker compose run --rm -e EYWA_RPC_ENDPOINT=https://your-rpc-endpoint app python -m app.scripts.scan --start-block 24078090 --end-block 24078589
```

Запустить совместный сценарий внутри контейнера `app`:

```powershell
docker compose up -d clickhouse
docker compose run --rm -e EYWA_RPC_ENDPOINT=https://your-rpc-endpoint app python -m app.scripts.scan_sequence --start-block 24078090 --end-block 24078589 --runs 2 --pool-source mixed
```

Создать новую ревизию Alembic:

```powershell
alembic revision -m "describe change"
```

## Основные таблицы

- `traders` - отслеживаемые адреса трейдеров
- `transactions` - найденные транзакции трейдеров и конкурентов
- `swaps` - swap-события, привязанные к транзакциям
- `tokens` - метаданные токенов
- `liquidity_pools` - пулы ликвидности
- `dexes` - DEX/factory-справочник
