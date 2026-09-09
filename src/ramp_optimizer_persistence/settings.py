"""Centralized database configuration without connection side effects."""

from dataclasses import dataclass
import os
from typing import Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


DATABASE_URL_ENVIRONMENT_VARIABLE = "RAMP_OPTIMIZER_DATABASE_URL"
DEFAULT_DATABASE_URL = "sqlite:///./ramp_optimizer.db"


class DatabaseConfigurationError(ValueError):
    """Raised for unusable database configuration without echoing its value."""


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    database_url: str = DEFAULT_DATABASE_URL

    def __post_init__(self) -> None:
        try:
            parsed = make_url(self.database_url)
        except (ArgumentError, TypeError) as error:
            raise DatabaseConfigurationError("Database URL is invalid.") from error
        if not parsed.drivername:
            raise DatabaseConfigurationError("Database URL is invalid.")

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "DatabaseSettings":
        source = os.environ if environment is None else environment
        return cls(source.get(DATABASE_URL_ENVIRONMENT_VARIABLE, DEFAULT_DATABASE_URL))
