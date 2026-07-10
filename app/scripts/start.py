from __future__ import annotations

import uvicorn

from .common import prepare_cli_runtime


def main():
    settings = prepare_cli_runtime()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        log_config=None,
        log_level="debug" if settings.debug else "info",
    )


if __name__ == "__main__":
    main()
