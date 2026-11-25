"""Errors raised by the store."""


class StoreError(RuntimeError):
    """A store operation failed for a reason the caller should report."""


class SyncStateError(StoreError):
    """A sync run is unknown, stale, or in the wrong state."""
