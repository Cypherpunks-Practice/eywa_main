from __future__ import annotations

import logging
from datetime import datetime
from threading import Event, Lock, Thread
from zoneinfo import ZoneInfo

from sqlalchemy import text

from ..core.config import Settings
from ..core.database import session_scope
from ..utils.cron import CronExpression

logger = logging.getLogger(__name__)


__all__ = ["TradersCleanupService"]


class TradersCleanupService:
    """Периодически удаляет из `traders` малоактивных трейдеров.

    В ClickHouse нет планировщика произвольного SQL (refreshable MV — только
    SELECT→таблица, TTL не умеет джойн к transactions), поэтому DELETE-мутацию
    по расписанию гоняет само приложение. Структура зеркалит ScanSchedulerService,
    но воркер синхронный — это просто SQL, без asyncio.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._stop_event = Event()
        self._job_lock = Lock()
        self._scheduler_thread: Thread | None = None
        self._worker_thread: Thread | None = None
        self._timezone = ZoneInfo(settings.scheduler_timezone)
        self._schedule: CronExpression | None = None
        if settings.traders_cleanup_enabled and settings.traders_cleanup_cron is not None:
            self._schedule = CronExpression(settings.traders_cleanup_cron)

    def start(self) -> None:
        if not self._settings.traders_cleanup_enabled:
            logger.info("Traders cleanup is disabled")
            return
        if self._schedule is None:
            raise ValueError(
                "EYWA_TRADERS_CLEANUP_ENABLED=true requires EYWA_TRADERS_CLEANUP_CRON to be set"
            )
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            return

        self._stop_event.clear()
        self._scheduler_thread = Thread(
            target=self._run_loop,
            name="eywa-traders-cleanup-scheduler",
            daemon=True,
        )
        self._scheduler_thread.start()
        logger.info(
            "Traders cleanup scheduler started: cron='%s', timezone='%s', min_transactions=%s",
            self._schedule.expression,
            self._settings.scheduler_timezone,
            self._settings.traders_cleanup_min_transactions,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=5)
        if self._worker_thread is not None and self._worker_thread.is_alive():
            logger.info("Traders cleanup is still running and will stop with the process")

    def _run_loop(self) -> None:
        if self._schedule is None:
            return

        while not self._stop_event.is_set():
            now = datetime.now(self._timezone)
            next_run = self._schedule.next_after(now)
            wait_seconds = max((next_run - now).total_seconds(), 0)
            logger.info("Next traders cleanup at %s", next_run.isoformat())

            if self._stop_event.wait(wait_seconds):
                return

            if not self._job_lock.acquire(blocking=False):
                logger.warning(
                    "Traders cleanup skipped because the previous run is still in progress"
                )
                continue

            self._worker_thread = Thread(
                target=self._execute_cleanup,
                name="eywa-traders-cleanup-worker",
                daemon=True,
            )
            self._worker_thread.start()

    def _execute_cleanup(self) -> None:
        min_transactions = int(self._settings.traders_cleanup_min_transactions)
        try:
            with session_scope() as session:
                candidate_row = session.execute(
                    text(self._build_candidate_count_sql())
                ).one_or_none()
                candidates = int(candidate_row[0]) if candidate_row else 0
                logger.info(
                    "Traders cleanup: %s trader(s) with < %s transaction(s) queued for deletion",
                    f"{candidates:,}",
                    min_transactions,
                )
                session.execute(text(self._build_cleanup_sql()))
            logger.info(
                "Traders cleanup mutation submitted (min_transactions=%s)", min_transactions
            )
        except Exception:
            logger.exception("Traders cleanup failed")
        finally:
            try:
                self._job_lock.release()
            except RuntimeError:
                logger.warning("Job lock was already released")

    def _build_cleanup_sql(self) -> str:
        """Порог подставляется как int из настроек (доверенное значение, не ввод
        пользователя), поэтому интерполяция в текст безопасна.
        """
        return (
            "ALTER TABLE traders DELETE WHERE contract_address IN ("
            f"{self._candidate_select()})"
        )

    def _build_candidate_count_sql(self) -> str:
        """Сколько трейдеров подпадёт под удаление — для лога перед мутацией."""
        return f"SELECT count() FROM ({self._candidate_select()})"

    def _candidate_select(self) -> str:
        # Трейдеры с числом сделок меньше порога (LEFT JOIN → у трейдера без сделок
        # count() = 0).
        min_transactions = int(self._settings.traders_cleanup_min_transactions)
        return (
            " SELECT t.contract_address"
            " FROM traders AS t"
            " LEFT JOIN transactions AS tx ON tx.trader_address = t.contract_address"
            " GROUP BY t.contract_address"
            f" HAVING count(tx.trader_address) < {min_transactions}"
        )
