from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Consent Management Platform"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000

    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5433/consent_platform"

    JWT_SECRET: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    CONTEXT_TOKEN_EXPIRE_MINUTES: int = 15
    BCRYPT_ROUNDS: int = 12

    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    INTEGRATION_API_KEY: str = "dev-demo-integration-key-2026"

    # Cross-app URLs: the CRM portal (8005) hands customers to the consent
    # portal (5173) and receives them back after consent management.
    CONSENT_PORTAL_URL: str = "http://localhost:5173"
    CRM_PORTAL_URL: str = "http://localhost:8005"

    # Consent platform API used by the standalone CRM backend service (8001).
    CONSENT_API_URL: str = "http://127.0.0.1:8000"

    GROQ_API_KEY: str = ""

    # AES-256-GCM field-level encryption key (base64-encoded, 44 chars).
    # Generate with: python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
    FIELD_ENCRYPTION_KEY: str = ""

    SEED_ADMIN_USERNAME: str = "admin"
    SEED_ADMIN_PASSWORD: str = "Admin@1234"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
