def provider_supports_batch_requests(provider: object) -> bool:
    """Return whether the configured provider exposes batch requests."""

    return hasattr(provider, "make_batch_request")
