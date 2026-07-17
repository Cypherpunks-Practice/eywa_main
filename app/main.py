from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from inspect import isawaitable
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, FastAPI

from .core.config import get_settings
from .core.container import Container
from .services.healthcheck_service import HealthcheckService
from .services.scan_scheduler_service import ScanSchedulerService
from .services.traders_cleanup_service import TradersCleanupService

settings = get_settings()


async def _maybe_await(result: object) -> None:
    if isawaitable(result):
        await result


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    container: Container = app.state.container  # type: ignore[attr-defined]
    scheduler: ScanSchedulerService | None = None
    traders_cleanup: TradersCleanupService | None = None
    await _maybe_await(container.init_resources())
    if settings.scheduler_enabled:
        scheduler = ScanSchedulerService(
            scan_orchestrator_factory=container.scan_orchestrator_service,
            settings=settings,
        )
        scheduler.start()
        app.state.scan_scheduler = scheduler
    # Независимый от scan-шедулера планировщик чистки traders: если scan завис,
    # чистка продолжает работать (и наоборот).
    if settings.traders_cleanup_enabled:
        traders_cleanup = TradersCleanupService(settings=settings)
        traders_cleanup.start()
        app.state.traders_cleanup_scheduler = traders_cleanup
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.stop()
        if traders_cleanup is not None:
            traders_cleanup.stop()
        await _maybe_await(container.shutdown_resources())
        container.unwire()


def create_app() -> FastAPI:
    container = Container()
    container.wire(modules=[__name__])

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.container = container
    app.include_router(health_router, tags=["system"])

    return app


health_router = APIRouter()
HealthcheckDependency = Annotated[
    HealthcheckService,
    Depends(Provide[Container.healthcheck_service]),
]


@health_router.get("/health")
@inject
async def health(service: HealthcheckDependency) -> dict[str, str]:
    return await service.healthcheck()


app = create_app()
