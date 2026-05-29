from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "NeuroNOC"
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    DATABASE_URL: str = (
        "postgresql+psycopg://neuronoc:neuronoc_dev_password@localhost:5433/neuronoc"
    )
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b-instruct"


settings = Settings()
