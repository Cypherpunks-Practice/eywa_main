from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from threading import Event, Lock, Thread
from zoneinfo import ZoneInfo

from ..core.config import Settings
from ..utils.cron import CronExpression

logger = logging.getLogger(__name__)


__all__ = ["ScanSchedulerService"]


class ScanSchedulerService:
    def __init__(
        self,
        scan_orchestrator_factory: Callable[[], object],
        settings: Settings,
    ):
        self._settings = settings
        self._scan_orchestrator_factory = scan_orchestrator_factory
        self._stop_event = Event()
        self._job_lock = Lock()
        self._scheduler_thread: Thread | None = None
        self._worker_thread: Thread | None = None
        self._timezone = ZoneInfo(self._settings.scheduler_timezone)
        self._schedule: CronExpression | None = None
        if self._settings.scheduler_enabled and self._settings.scheduler_cron is not None:
            self._schedule = CronExpression(self._settings.scheduler_cron)

    def start(self):
        if not self._settings.scheduler_enabled:
            logger.info("Scan scheduler is disabled")
            return
        if self._schedule is None:
            raise ValueError("EYWA_SCHEDULER_ENABLED=true requires EYWA_SCHEDULER_CRON to be set")
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            return

        self._stop_event.clear()
        self._scheduler_thread = Thread(
            target=self._run_loop,
            name="eywa-scan-scheduler",
            daemon=True,
        )
        self._scheduler_thread.start()
        logger.info(
            "Scan scheduler started: cron='%s', timezone='%s', runs=%s",
            self._schedule.expression,
            self._settings.scheduler_timezone,
            self._settings.scheduler_runs,
        )

    def stop(self):
        self._stop_event.set()
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=5)
        if self._worker_thread is not None and self._worker_thread.is_alive():
            logger.info("Scheduled scan is still running and will stop with the process")

    def _run_loop(self):
        if self._schedule is None:
            return

        while not self._stop_event.is_set():
            now = datetime.now(self._timezone)
            next_run = self._schedule.next_after(now)
            wait_seconds = max((next_run - now).total_seconds(), 0)
            logger.info("Next scheduled scan at %s", next_run.isoformat())

            if self._stop_event.wait(wait_seconds):
                return

            if not self._job_lock.acquire(blocking=False):
                logger.warning("Scheduled scan skipped because the previous run is still in progress")
                continue

            self._worker_thread = Thread(
                target=self._execute_scheduled_scan,
                name="eywa-scan-worker",
                daemon=True,
            )
            self._worker_thread.start()

    def _execute_scheduled_scan(self):
        try:
            asyncio.run(self._execute_scheduled_scan_async())
        except Exception:
            logger.exception("Scheduled scan failed")
        finally:
            try:
                self._job_lock.release()
            except RuntimeError:
                logger.warning("Job lock was already released")

    async def _execute_scheduled_scan_async(self):
        orchestrator = self._scan_orchestrator_factory()
        start_block, end_block = await orchestrator.resolve_auto_scan_window()
        for run_index in range(1, self._settings.scheduler_runs + 1):
            logger.info(
                "Starting scheduled scan sequence run %s/%s for fixed blocks %s-%s",
                run_index,
                self._settings.scheduler_runs,
                f"{start_block:,}",
                f"{end_block:,}",
            )
            result = await orchestrator.run_scan_sequence(
                start_block=start_block,
                end_block=end_block,
                persist=True,
            )
            reverse_transactions = (
                result.reverse_result.total_transaction_count
                if result.reverse_result is not None
                else 0
            )
            logger.info(
                "Scheduled scan sequence run %s/%s completed for blocks %s-%s: forward tx=%s, forward swaps=%s, reverse tx=%s",
                run_index,
                self._settings.scheduler_runs,
                f"{result.forward_result.request.start_block:,}",
                f"{result.forward_result.request.end_block:,}",
                f"{result.forward_result.transaction_count:,}",
                f"{result.forward_result.enriched_swap_count:,}",
                f"{reverse_transactions:,}",
            )
