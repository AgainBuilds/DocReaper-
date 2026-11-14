
"""DocReaper configuration."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Database
    database_url: str = "sqlite:///./docreaper.db"

    # Existing cloud storage
    gcs_bucket: str = ""
    gcs_prefix: str = "books"
    local_library_dir: str = "library"

    # Pipeline
    target_unique_books: int = 270
    max_download_mb: int = 100
    download_delay_seconds: float = 1.0
    download_retries: int = 3
    request_timeout: int = 60
    user_agent: str = "DocReaper/1.0 (open-access academic research)"

    near_duplicate_threshold: float = 0.85
    min_text_chars: int = 1000
    pages_per_subject: int = 3
    subjects: list[str] = [
        "computer science",
        "mathematics",
        "physics",
        "chemistry",
        "engineering",
        "statistics",
    ]

    # Dashboard security
    control_token: str = ""

    # Google Drive OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = (
        "https://docreaper.onrender.com/auth/google/callback"
    )
    google_oauth_state_secret: str = ""
    google_drive_refresh_token: str = ""
    google_drive_folder_id: str = ""


settings = Settings()
