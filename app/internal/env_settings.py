import pathlib

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.internal.auth.login_types import LoginTypeEnum


class DBSettings(BaseModel):
    sqlite_path: str = "db.sqlite"
    """Relative path to the sqlite database given the config directory. If absolute, it ignores the config dir location."""
    use_postgres: bool = False
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "audiobookrequest"
    postgres_user: str = "abr"
    postgres_password: str = "password"
    postgres_ssl_mode: str = "prefer"
    
    # Connection Pool Configuration
    pool_size: int = 10
    """SQLAlchemy connection pool size (number of connections to maintain in pool)"""
    max_overflow: int = 20
    """Maximum number of overflow connections beyond pool_size"""
    pool_timeout: int = 30
    """Timeout (seconds) to wait for a connection from the pool"""
    pool_pre_ping: bool = True
    """Enable ping to detect stale connections before using them"""


class ApplicationSettings(BaseModel):
    debug: bool = False
    openapi_enabled: bool = False
    config_dir: str = "/config"
    port: int = 8000
    version: str = "local"
    log_level: str = "INFO"
    """Logging level (DEBUG, INFO, WARNING, ERROR)"""
    log_format: str = "text"
    """Log format: 'text' for human-readable, 'json' for machine-readable"""
    log_file: str | None = None
    """Optional log file path (relative to config_dir/logs/). If not set, logs to stdout only"""
    base_url: str = ""

    default_region: str = "us"
    """Default region used in the search"""

    force_login_type: str = ""
    """Forces the login type used. If set, the login type cannot be changed in the UI."""

    init_root_username: str = ""
    init_root_password: str = ""

    enable_metadata_enrichment: bool = True
    """Enable metadata enrichment for virtual books using Google Books API"""

    metadata_cache_expiry_days: int = 30
    """Number of days to cache metadata enrichment results"""

    google_books_api_key: str = ""
    """Optional Google Books API key (works without key but has rate limits)"""

    # Author Relevance Ranking Settings
    enable_author_relevance_ranking: bool = True
    """Enable author relevance ranking for search results (available_only mode only)"""

    author_match_threshold: float = 70.0
    """Minimum score to show in 'Best Matches' section (0-100)"""

    author_relevance_strict_mode_default: bool = False
    """Default state for strict author matching toggle"""

    enable_secondary_scoring: bool = True
    """Enable secondary scoring factors (title, recency, popularity)"""

    # Performance Settings
    max_concurrent_audible_requests: int = 15
    """Maximum concurrent Audible API requests (default: 15, recommended max: 30)"""

    # Cache TTL Settings (seconds)
    fuzzy_match_cache_ttl: int = 3600
    """TTL for fuzzy matching cache (default: 1 hour)"""

    ranking_cache_ttl: int = 1800
    """TTL for author ranking cache (default: 30 minutes)"""

    upgrade_attempt_cache_ttl: int = 86400
    """TTL for virtual book upgrade attempt cache (default: 24 hours)"""

    def get_force_login_type(self) -> LoginTypeEnum | None:
        if self.force_login_type.strip():
            try:
                login_type = LoginTypeEnum(self.force_login_type.strip().lower())
                if login_type == LoginTypeEnum.api_key:
                    raise ValueError(
                        "API key login type is not supported for forced login type."
                    )
                return login_type
            except ValueError:
                raise ValueError(f"Invalid force login type: {self.force_login_type}")
        return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(  # pyright: ignore[reportUnannotatedClassAttribute]
        env_prefix="ABR_",
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
        env_file=(".env.local", ".env"),
        extra="ignore",
    )

    db: DBSettings = DBSettings()
    app: ApplicationSettings = ApplicationSettings()

    def get_sqlite_path(self):
        if self.db.sqlite_path.startswith("/"):
            return self.db.sqlite_path
        return str(pathlib.Path(self.app.config_dir) / self.db.sqlite_path)
