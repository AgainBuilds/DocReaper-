
"""DocReaper configuration."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCREAPER_",
        env_file=".env",
        extra="ignore"
    )

    database_url: str = "sqlite:///./docreaper.db"
    user_agent: str = "DocReaper/2.1.0 (+contact: configure DOCREAPER_USER_AGENT)"
    control_token: str = ""

    request_timeout: float = 30.0
    max_download_mb: int = 100
    download_retries: int = 3
    download_delay_seconds: float = 1.0
    pages_per_subject: int = 2
    target_unique_books: int = 270
    near_duplicate_threshold: float = 0.5
    min_text_chars: int = 200

    gcs_bucket: str = ""
    gcs_prefix: str = "books"
    local_library_dir: str = "library"
    log_level: str = "INFO"

    subjects: list[str] = [
        "computer science",
        "programming",
        "algorithms",
        "data structures",
        "calculus",
        "linear algebra",
        "discrete mathematics",
        "statistics",
        "physics",
        "chemistry",
        "biology",
        "academic writing",
        "logic",
    ]


settings = Settings()
