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
    JWT_ISSUER: str = "consent360"
    JWT_AUDIENCE: str = "consent360-api"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ORG_REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    CONTEXT_TOKEN_EXPIRE_MINUTES: int = 15
    BCRYPT_ROUNDS: int = 12

    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    CORS_ALLOW_METHODS: str = "GET,POST,PUT,DELETE,PATCH"

    # R3-01: Tenant-bound API keys - no longer a shared static key.
    # Keys are per-tenant, bound to tenant_id, and hashed server-side.
    # The old INTEGRATION_API_KEY constant has been removed; keys are managed
    # through the /tenants/{id}/api-keys CRUD endpoints.
    INTEGRATION_API_KEY: str = ""

    CONSENT_PORTAL_URL: str = "http://localhost:5173"
    CRM_PORTAL_URL: str = "http://localhost:8005"
    CONSENT_API_URL: str = "http://127.0.0.1:8000"

    GROQ_API_KEY: str = ""

    FIELD_ENCRYPTION_KEY: str = ""
    HMAC_SEARCH_KEY: str = ""

    SEED_ADMIN_USERNAME: str = "admin"
    SEED_ADMIN_PASSWORD: str = "Admin@1234"

    # R3-01: API key rotation grace period
    API_KEY_GRACE_PERIOD_HOURS: int = 24

    # R3-02: Password and lockout settings
    PASSWORD_MIN_LENGTH: int = 12
    ACCOUNT_LOCKOUT_THRESHOLD: int = 5
    ACCOUNT_LOCKOUT_MINUTES: int = 30

    # R3-03: Fail-closed production mode - application refuses to start if
    # encryption key is missing in production.  Keep warning-only in dev.
    PRODUCTION_FAIL_CLOSED: bool = True

    # R3-03: Separate HMAC search key from AES encryption key
    HMAC_SEARCH_KEY: str = ""

    # R3-03: Fail-closed on missing JWT_SECRET in production
    JWT_SECRET_REQUIRED_IN_PROD: bool = True

    # R3-04: Alert thresholds
    BULK_EXPORT_THRESHOLD: int = 100
    OFF_HOURS_START: int = 22
    OFF_HOURS_END: int = 6

    # R3-06: Notification settings
    NOTIFICATION_EMAIL_HOST: str = ""
    NOTIFICATION_EMAIL_PORT: int = 587
    NOTIFICATION_EMAIL_USER: str = ""
    NOTIFICATION_EMAIL_PASSWORD: str = ""
    NOTIFICATION_SMS_API_URL: str = ""
    NOTIFICATION_SMS_API_KEY: str = ""

    # R3-07: Processor webhook settings
    PROCESSOR_ALERT_SLA_HOURS: int = 24

    # R3-05: OTP settings
    OTP_EXPIRE_MINUTES: int = 10
    OTP_MAX_ATTEMPTS: int = 5
    OTP_RATE_LIMIT_ATTEMPTS: int = 5
    OTP_RATE_LIMIT_WINDOW_MINUTES: int = 15

    # R3-05: Fiduciary assertion
    FIDUCIARY_SIGNING_KEY: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def cors_allow_methods_list(self) -> list[str]:
        return [m.strip() for m in self.CORS_ALLOW_METHODS.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()