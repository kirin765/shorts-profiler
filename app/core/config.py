from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql+psycopg2://shorts:shorts@localhost:5432/shorts_profiler"
    redis_url: str = "redis://localhost:6379/0"
    storage_path: str = "./storage"
    video_bucket_path: str = "videos"
    tmp_path: str = "tmp"
    queue_name: str = "shorts"
    cleanup_source_video: bool = True
    ytdlp_args: str = "--format mp4 --no-check-certificate"

    class Config:
        env_file = ".env"


settings = Settings()


def storage_root() -> Path:
    return Path(settings.storage_path).resolve()


def videos_dir() -> Path:
    return storage_root() / settings.video_bucket_path


def tmp_dir() -> Path:
    return storage_root() / settings.tmp_path
