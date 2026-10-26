"""Custom exceptions."""


class CollectorError(Exception):
    """Base collector exception."""


class CollectionError(CollectorError):
    """File collection failure."""


class PreprocessingError(CollectorError):
    """Preprocessing failure."""


class PersistenceError(CollectorError):
    """Database persistence failure."""
