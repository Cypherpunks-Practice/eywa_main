from __future__ import annotations


class EywaError(Exception):
    """Base exception for all eywa errors."""


class RpcError(EywaError):
    """Base exception for RPC communication errors."""


class RpcTransientError(RpcError):
    """Transient RPC error (timeouts, rate limits, network errors)."""


class RpcPermanentError(RpcError):
    """Permanent RPC error (invalid params, method not found)."""


class PersistenceError(EywaError):
    """Database operation error."""
