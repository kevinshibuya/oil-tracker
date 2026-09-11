"""Process configuration, read from the environment in exactly one place."""

from pydantic_settings import BaseSettings, SettingsConfigDict

# Host port 5434 rather than the 5433 the plan named: 5433 is taken on the
# machine this milestone is executed on. infra/docker-compose.yml maps to match.
DEFAULT_DATABASE_URL = "postgresql://oil:oil@localhost:5434/oil"


class Settings(BaseSettings):
    """Every environment-derived value the pipeline reads."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL
