from __future__ import annotations

import asyncio

from ..core.database import ping_clickhouse


class HealthcheckService:
    async def healthcheck(self) -> dict[str, str]:
        await asyncio.to_thread(ping_clickhouse)
        return {"status": "ok", "database": "ok"}
