"""Юнит-тесты планировщика чистки traders (без БД и сети).

Проверяем сборку SQL (структура/порог), поведение start() при выключенном сервисе
и без cron, и нормализацию cron из конфига.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.traders_cleanup_service import TradersCleanupService


def _service(*, min_tx: int = 3, enabled: bool = False, cron: str | None = None):
    settings = Settings(
        traders_cleanup_enabled=enabled,
        traders_cleanup_cron=cron,
        traders_cleanup_min_transactions=min_tx,
    )
    return TradersCleanupService(settings=settings)


def test_cleanup_sql_targets_traders_with_threshold() -> None:
    sql = _service(min_tx=3)._build_cleanup_sql()
    assert "ALTER TABLE traders DELETE" in sql
    assert "transactions" in sql
    assert "< 3" in sql


def test_cleanup_sql_threshold_is_configurable() -> None:
    assert "< 5" in _service(min_tx=5)._build_cleanup_sql()
    assert "< 5" not in _service(min_tx=3)._build_cleanup_sql()


def test_candidate_count_sql_is_select_count() -> None:
    sql = _service(min_tx=3)._build_candidate_count_sql()
    assert sql.strip().lower().startswith("select count()")
    assert "traders" in sql
    assert "< 3" in sql


def test_disabled_service_start_is_noop() -> None:
    service = _service(enabled=False)
    service.start()
    assert service._scheduler_thread is None


def test_enabled_without_cron_raises_on_start() -> None:
    service = _service(enabled=True, cron=None)
    with pytest.raises(ValueError):
        service.start()


def test_blank_cron_normalized_to_none() -> None:
    assert Settings(traders_cleanup_cron="   ").traders_cleanup_cron is None
