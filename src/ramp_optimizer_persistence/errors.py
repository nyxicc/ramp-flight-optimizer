"""Persistence errors with safe, stable meaning for application boundaries."""


class PersistenceError(RuntimeError):
    """Base class for persistence-layer failures."""


class ResourceNotFoundError(PersistenceError):
    """A well-formed resource identifier is unknown."""


class PersistenceIntegrityError(PersistenceError):
    """Stored data no longer matches its immutable integrity metadata."""


class DatabaseOperationError(PersistenceError):
    """A database operation failed without exposing driver details."""


class PersistenceConflictError(PersistenceError):
    """A client-correctable uniqueness or constraint conflict."""
