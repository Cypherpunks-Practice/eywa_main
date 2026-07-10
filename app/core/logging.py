from __future__ import annotations

import logging
import sys
from collections.abc import Mapping, Sequence

from .config import Settings, get_settings

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class SensitiveValueFilter(logging.Filter):
    def __init__(self, replacements: Mapping[str, str]):
        super().__init__()
        self._replacements = {
            sensitive: replacement
            for sensitive, replacement in replacements.items()
            if sensitive
        }

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._sanitize(record.msg)
        record.args = self._sanitize(record.args)
        return True

    def _sanitize(self, value: object) -> object:
        if isinstance(value, str):
            sanitized_value = value
            for sensitive, replacement in self._replacements.items():
                sanitized_value = sanitized_value.replace(sensitive, replacement)
            return sanitized_value

        if isinstance(value, tuple):
            return tuple(self._sanitize(item) for item in value)

        if isinstance(value, list):
            return [self._sanitize(item) for item in value]

        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return type(value)(self._sanitize(item) for item in value)

        if isinstance(value, Mapping):
            return {
                key: self._sanitize(item)
                for key, item in value.items()
            }

        return value


def configure_logging(settings: Settings | None = None) -> None:
    resolved_settings = settings or get_settings()
    logging.basicConfig(
        level=logging.DEBUG if resolved_settings.debug else logging.INFO,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        stream=sys.stdout,
        force=True,
    )
    sensitive_value_filter = SensitiveValueFilter(
        replacements={
            resolved_settings.rpc_endpoint: "EYWA_RPC_ENDPOINT",
        }
    )
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(sensitive_value_filter)
