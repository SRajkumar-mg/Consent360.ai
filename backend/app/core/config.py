import base64
import os
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _aes_key_problem(value: str, label: str) -> str | None:
    """Return a human-readable problem with a base64 32-byte key, or None.

    Mirrors app/core/encryption.py::_decode_b64_key exactly. It exists so
    `Settings.secret_config_issues()` can answer "is this key actually
    USABLE" at startup instead of only "is it truthy": a whitespace-only or
    wrong-length FIELD_ENCRYPTION_KEY passed the old bare truthiness gate
    and then raised an uncaught ValueError out of `_load_keyring()` on the
    first encrypt/decrypt - mid-request, in production, long after the one
    moment the operator was watching.
    """
    candidate = (value or "").strip()
    if not candidate:
        return f"{label} is empty or whitespace-only"
    try:
        raw = base64.urlsafe_b64decode(candidate)
    except Exception:
        return f"{label} is not valid url-safe base64"
    if len(raw) != 32:
        return f"{label} must decode to 32 bytes (AES-256), got {len(raw)}"
    return None

# Fields whose real value may be delivered as a mounted file instead of an
# inline env var / `.env` line — the standard Docker/Kubernetes "secret
# file" convention (also used by Docker Compose secrets and most secret-
# manager sidecars/CSI drivers). Setting "<NAME>_FILE=/run/secrets/foo"
# makes `_load_secrets_from_files` below read the file's content and use it
# as `<NAME>`, so none of these ever needs to hold its real value inside the
# repo's `.env` (R3-03 / H-09: "secrets moved out of the repo .env into a
# secret manager").
_SECRET_FIELDS = (
    "JWT_SECRET",
    "FIELD_ENCRYPTION_KEY",
    "FIELD_ENCRYPTION_KEY_PREVIOUS",
    "HMAC_SEARCH_KEY",
    "HMAC_SEARCH_KEY_PREVIOUS",
    "INTEGRATION_API_KEY",
    "KMS_ENCRYPTED_DATA_KEY",
    "SMTP_PASSWORD",
    "DATABASE_URL",
)


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
    # RFC 8725 ("JWT Best Current Practices") recommends every token carry
    # `iss`/`aud` and every verifier check them explicitly rather than
    # trusting the signature alone. Both are configurable so a deployment
    # can rename them without a code change; `decode_token`
    # (app/core/security.py) rejects any staff/context token whose `iss`
    # or `aud` doesn't match.
    JWT_ISSUER: str = "consent360-api"
    JWT_AUDIENCE: str = "consent360-staff"
    # Context tokens (customer self-service) are consumed by a different
    # audience than staff tokens - see app/core/security.py's
    # create_context_token / app/api/deps.py's verify_context_token.
    JWT_AUDIENCE_CONTEXT: str = "consent360-context"
    # Organization-portal tokens (app/api/routes/organizations.py). A third,
    # separate audience: R3-02's RFC 8725 hardening was applied to the staff
    # and context schemes but skipped the org scheme entirely, which minted
    # tokens with no `iss` and no `aud` at all. Distinct from JWT_AUDIENCE so
    # an org token can never satisfy a staff verifier's audience check even
    # if some future change let its ctx/type/sub_type through.
    JWT_AUDIENCE_ORG: str = "consent360-org"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    CONTEXT_TOKEN_EXPIRE_MINUTES: int = 15
    BCRYPT_ROUNDS: int = 12
    SCHEDULER_ENABLED: bool = False

    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    INTEGRATION_API_KEY: str = "dev-demo-integration-key-2026"
    # A single unbound key that can stamp any source_app (R3-01: "no shared
    # static key"). Off by default; enable only for a caller that genuinely
    # cannot yet be issued a tenant-bound `c360_...` key (see
    # `seed_api_keys.py` and app/core/api_keys.py). main.py logs a warning
    # at startup whenever this is true.
    ALLOW_LEGACY_INTEGRATION_KEY: bool = False

    RESTRICTED_COUNTRIES: str = ""

    @property
    def restricted_country_list(self) -> list[str]:
        return [c.strip().upper() for c in self.RESTRICTED_COUNTRIES.split(",") if c.strip()]

    def is_restricted_destination(self, country: str | None) -> bool:
        return bool(country) and country.strip().upper() in self.restricted_country_list

    # Cross-app URLs. CONSENT_PORTAL_URL is the admin console's self-service
    # portal (app/api/routes/crm.py's handoff sends principals to
    # `{CONSENT_PORTAL_URL}/portal/consent`); the other three are each demo
    # site's OWN address, used only to build the "Return to site" link that
    # sends a principal back where they actually came from (see
    # app/api/routes/crm.py::_consent_portal_url / _return_base_for). Each
    # one previously fell back to CRM_PORTAL_URL regardless of which site
    # handed the customer off, and CRM_PORTAL_URL itself defaulted to the
    # STAFF admin console's port - so "Return to site" never returned to
    # Codex or SkillLearn, and not even to the CRM demo site itself.
    CONSENT_PORTAL_URL: str = "http://localhost:8005"
    CRM_PORTAL_URL: str = "http://localhost:8008"
    CODEX_PORTAL_URL: str = "http://localhost:5174"
    SKILLLEARN_PORTAL_URL: str = "http://localhost:5175"

    # Consent platform API used by the standalone CRM backend service (8001).
    CONSENT_API_URL: str = "http://127.0.0.1:8000"

    GROQ_API_KEY: str = ""

    # AES-256-GCM field-level encryption key (base64-encoded, 44 chars).
    # Generate with: python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
    FIELD_ENCRYPTION_KEY: str = ""
    # Comma-separated list of previously-active FIELD_ENCRYPTION_KEY values,
    # each still base64/32-byte, kept ONLY so rows encrypted before the most
    # recent rotation keep decrypting. Never used to encrypt new data — see
    # app/core/encryption.py's key-ring / `rotate_column_key`.
    FIELD_ENCRYPTION_KEY_PREVIOUS: str = ""

    # HMAC key for deterministic search digests (Customer/User *_search
    # columns). Deliberately separate from FIELD_ENCRYPTION_KEY: reusing one
    # AES key both to encrypt data and to key an HMAC is a textbook
    # key-reuse anti-pattern (H-01/T-07). Same generation command as above.
    # Falls back to FIELD_ENCRYPTION_KEY when unset so existing installs
    # keep matching already-stored digests without a forced rehash. Set
    # this and run `python -m scripts.rotate_encryption_keys --hmac` to
    # fully separate them; the previously-effective key is retained for
    # LOOKUP automatically (app/core/encryption.py::_load_hmac_keyring),
    # so the change needs no downtime and the rehash run is an
    # optimisation rather than a prerequisite.
    HMAC_SEARCH_KEY: str = ""
    HMAC_SEARCH_KEY_PREVIOUS: str = ""

    # Where the AES data key itself comes from. "env" (default) reads
    # FIELD_ENCRYPTION_KEY directly. "kms" fetches it from AWS KMS via
    # envelope decryption: KMS_ENCRYPTED_DATA_KEY is the small ciphertext
    # blob produced once by `aws kms encrypt`, decrypted at process start
    # into the real 32-byte key, which is then never itself stored anywhere.
    # See app/core/encryption.py::_fetch_kms_key. `boto3` is imported lazily
    # so KEY_PROVIDER=env installs never need it.
    KEY_PROVIDER: str = "env"
    KMS_KEY_ID: str = ""
    KMS_ENCRYPTED_DATA_KEY: str = ""
    AWS_REGION: str = ""

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "no-reply@consent360.local"
    PORTAL_REQUIRE_VERIFICATION: bool = True

    SEED_ADMIN_USERNAME: str = "admin"
    SEED_ADMIN_PASSWORD: str = "Admin@1234"

    # --- R3-04: access logging, metrics, alerting -------------------------
    # Redis-backed rate limiting/token-revocation store. Empty = fall back
    # to the in-memory implementation (fine for a single dev process; not
    # durable across restarts or multiple workers — see
    # app/core/utils.py::build_rate_limiter).
    REDIS_URL: str = ""
    LOG_FORMAT: str = "json"  # "json" | "text"
    LOG_FILE: str = ""  # if set, also write rotating JSON lines here
    LOG_RETENTION_DAYS: int = 366  # ~1 year, per R3-04's retention requirement
    ALERT_BULK_EXPORT_THRESHOLD: int = 100
    ALERT_OFFHOURS_START_HOUR: int = 20  # IST business hours: 08:00-20:00
    ALERT_OFFHOURS_END_HOUR: int = 8
    ALERT_FAILED_LOGIN_THRESHOLD: int = 5
    ALERT_FAILED_LOGIN_WINDOW_MINUTES: int = 15
    ALERT_WEBHOOK_URL: str = ""

    # --- R3-02: staff auth hardening ---------------------------------------
    # Must stay False: `_reject_unhonoured_flags` below REFUSES TO START if
    # it is set, because nothing in this build enforces it and a flag that
    # silently does nothing is worse than no flag at all. See that
    # validator, and app/core/mfa.py, for what unblocks it.
    MFA_ENFORCED: bool = False
    # Hard lockout is counted per (username, source IP) pair, not per
    # username - see app/api/routes/auth.py::login for the DoS trade-off
    # this resolves and the residual risk it accepts.
    ACCOUNT_LOCKOUT_THRESHOLD: int = 5
    ACCOUNT_LOCKOUT_WINDOW_MINUTES: int = 15
    # Username-wide brake that replaces the username-wide hard lock: a
    # delay applied only after a WRONG password, doubling per failure past
    # ACCOUNT_LOCKOUT_THRESHOLD and capped here. 0 disables it.
    LOGIN_FAILURE_DELAY_MAX_SECONDS: float = 4.0
    PASSWORD_MIN_LENGTH: int = 12

    # --- R3-12: data residency / SDF readiness flags -----------------------
    # These are DECLARATIVE, operator-set flags, not an enforcement
    # mechanism: nothing in this codebase currently pins the primary
    # database, backups, or the LLM/notification processors to a physical
    # region. They exist so (a) a deployment can assert its actual residency
    # posture in one place instead of scattered infra config, (b)
    # `production_issues()` below can catch one narrow, purely internal
    # self-contradiction (asserting India-only residency without recording
    # which region that claim is about), and (c) an SDF notification or a
    # DPIA has a single source of truth to cite. Neither this nor (b) checks
    # the claim against reality - see
    # backend/docs/compliance/DATA_RESIDENCY_SDF_READINESS.md for what is
    # actually enforced today versus documented intent only.
    DATA_RESIDENCY_PRIMARY_DB_REGION: str = "unspecified"  # e.g. "IN-MUMBAI"; "unspecified" is a signal, not a default to ship with
    DATA_RESIDENCY_BACKUP_REGION: str = "unspecified"
    DATA_RESIDENCY_ASSERT_INDIA_ONLY: bool = False  # operator claims all storage (primary + backups) is India-resident
    # R3-12: BRD's "Significant Data Fiduciary" register readiness. This does
    # NOT determine SDF status (that is a government notification under
    # s.10 of the Act, not a self-declaration) - it only marks whether the
    # DPIA pack and algorithm register in backend/docs/compliance/ should be
    # treated as "must be current" by process (e.g. a CI/release check a
    # future task could add) versus "prepared ahead of time, not yet required".
    SDF_NOTIFIED: bool = False

    # --- R3-12: backup policy flags -----------------------------------------
    # Documented-intent flags for the backup policy
    # (backend/docs/compliance/BACKUP_POLICY.md), not an enforcement
    # mechanism - no code in this repo currently takes or ships a backup.
    # BACKUP_ENCRYPTION_ENABLED being false does not mean backups are
    # unencrypted in practice; it means this deployment has not asserted
    # that they are, which is exactly the honesty gap production_issues()
    # below closes for a production environment.
    BACKUP_ENCRYPTION_ENABLED: bool = False
    BACKUP_RETENTION_DAYS: int = 35
    BACKUP_RPO_MINUTES: int = 60  # policy target: at most this much data may be lost on restore
    BACKUP_RTO_MINUTES: int = 240  # policy target: time to restore service from a backup

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @model_validator(mode="after")
    def _load_secrets_from_files(self) -> "Settings":
        for name in _SECRET_FIELDS:
            file_path = os.environ.get(f"{name}_FILE")
            if file_path and os.path.isfile(file_path):
                with open(file_path, "r", encoding="utf-8") as fh:
                    setattr(self, name, fh.read().strip())
        return self

    @model_validator(mode="after")
    def _reject_unhonoured_flags(self) -> "Settings":
        """Refuse to construct settings that PROMISE a control this build
        cannot deliver. Deliberately not part of `production_issues()`: a
        flag that silently does nothing is at its most dangerous in the
        non-production environment where someone sets it, sees no error,
        and concludes the control is on.

        `MFA_ENFORCED` is the whole list today. It was read by nothing at
        all - setting it to True changed no behaviour anywhere - so it
        offered pure false assurance to whoever set it. Honouring it needs
        MFA enrollment to survive a restart, i.e. the `users.mfa_secret` /
        `mfa_enabled` / `mfa_backup_codes` columns described in
        app/core/mfa.py's module docstring; app/models/entities.py and the
        migration chain are owned by another lane, so the columns are
        queued rather than added here. Until they land, the honest
        behaviour is to fail loudly at startup rather than to accept the
        flag and ignore it. Opt-in MFA (/auth/mfa/enroll -> confirm ->
        required at /auth/mfa/verify-login) is unaffected and keeps working
        for whichever user turns it on.
        """
        if self.MFA_ENFORCED:
            raise ValueError(
                "MFA_ENFORCED=true is not supported by this build and would be silently "
                "ignored: staff MFA enrollment is held in process memory only "
                "(app/core/mfa.py), so enforcing it would lock every staff account out on "
                "the next restart. Unset MFA_ENFORCED. Enforcement is unblocked by the "
                "users.mfa_secret/mfa_enabled/mfa_backup_codes columns tracked in the "
                "R3-02 follow-up; opt-in MFA per user works today without this flag."
            )
        return self

    def secret_config_issues(self) -> list[str]:
        """Secrets that are present but UNUSABLE, independent of
        ENVIRONMENT so tests (and a dev who wants to check) can call it
        directly.

        The distinction this draws that `production_issues()`'s original
        truthiness checks did not: an *absent* key is a policy decision
        (refuse in production, warn in dev), whereas a *malformed* key is a
        latent crash. `"   "` and a 31-byte key both passed the old
        `if not self.FIELD_ENCRYPTION_KEY` gate and then raised an uncaught
        ValueError from encryption.py on the first row written.
        """
        issues: list[str] = []
        # Raw truthiness on purpose (not `.strip()`): a value that is
        # PRESENT but whitespace-only must be reported as the nonsense it
        # is, rather than falling through to production_issues()' "is not
        # set" branch, which would tell the operator to set a variable
        # they can plainly see they already set.
        if self.FIELD_ENCRYPTION_KEY:
            problem = _aes_key_problem(self.FIELD_ENCRYPTION_KEY, "FIELD_ENCRYPTION_KEY")
            if problem:
                issues.append(problem)
        for label, csv in (
            ("FIELD_ENCRYPTION_KEY_PREVIOUS", self.FIELD_ENCRYPTION_KEY_PREVIOUS),
            ("HMAC_SEARCH_KEY_PREVIOUS", self.HMAC_SEARCH_KEY_PREVIOUS),
        ):
            for index, entry in enumerate(e for e in csv.split(",") if e.strip()):
                problem = _aes_key_problem(entry, f"{label}[{index}]")
                if problem:
                    issues.append(problem)
        if self.HMAC_SEARCH_KEY:
            problem = _aes_key_problem(self.HMAC_SEARCH_KEY, "HMAC_SEARCH_KEY")
            if problem:
                issues.append(problem)
        if self.KEY_PROVIDER not in ("env", "kms"):
            issues.append(f"KEY_PROVIDER must be 'env' or 'kms', got {self.KEY_PROVIDER!r}")
        if self.KEY_PROVIDER == "kms" and self.KMS_ENCRYPTED_DATA_KEY.strip():
            try:
                base64.b64decode(self.KMS_ENCRYPTED_DATA_KEY.strip(), validate=True)
            except Exception:
                issues.append("KMS_ENCRYPTED_DATA_KEY is not valid base64")
        return issues

    def production_issues(self) -> list[str]:
        """Hard security defaults that must never run with
        ENVIRONMENT=production. A non-empty return means app startup must
        refuse to proceed (see app/main.py's lifespan) — the concrete
        mechanism behind R3-03/H-09's "fails closed in production"."""
        if self.ENVIRONMENT != "production":
            return []
        issues: list[str] = []
        # Present-but-unusable secrets first: an empty key correctly refused
        # to start here already, but a whitespace-only or wrong-length one
        # sailed past the truthiness check and blew up later, inside a
        # request, out of encryption.py.
        issues.extend(self.secret_config_issues())
        if not self.FIELD_ENCRYPTION_KEY and self.KEY_PROVIDER != "kms":
            issues.append("FIELD_ENCRYPTION_KEY is not set (and KEY_PROVIDER is not 'kms')")
        if self.KEY_PROVIDER == "kms" and not (self.KMS_KEY_ID.strip() and self.KMS_ENCRYPTED_DATA_KEY.strip()):
            issues.append("KEY_PROVIDER=kms requires both KMS_KEY_ID and KMS_ENCRYPTED_DATA_KEY")
        # `.strip()` throughout: a JWT_SECRET of 40 spaces is 40 characters
        # long and passed the old length check while being no secret at all.
        if not self.JWT_SECRET.strip() or self.JWT_SECRET.strip() == "change-me" or len(self.JWT_SECRET.strip()) < 32:  # noqa: E501
            issues.append(
                "JWT_SECRET is missing/whitespace-only, the default 'change-me', or shorter "
                "than 32 non-whitespace characters"
            )
        # R3-02/H-02 DoD: "logout invalidates tokens immediately". With no
        # REDIS_URL the revocation list is a process-local dict
        # (app/core/token_revocation.py), so that holds only until the
        # issuing process restarts - confirmed live: log out, restart,
        # replay the revoked access token, get 200. A durable revocation
        # store needs a `users.token_version` column (entities.py + a
        # migration, owned by another lane), so this gate makes the gap
        # impossible to DEPLOY into unnoticed rather than papering over it.
        # It also covers the rate limiters, which share REDIS_URL.
        if not self.REDIS_URL.strip():
            issues.append(
                "REDIS_URL is not set: token revocation (and the login/context rate limiters) "
                "would be per-process and lost on restart, so a logged-out token becomes valid "
                "again after a deploy. Configure Redis, or land the durable "
                "users.token_version revocation column before running in production"
            )
        if self.ALLOW_LEGACY_INTEGRATION_KEY:
            issues.append("ALLOW_LEGACY_INTEGRATION_KEY must be disabled in production")
        if self.INTEGRATION_API_KEY == "dev-demo-integration-key-2026":
            issues.append("INTEGRATION_API_KEY is still the default demo value")
        if "*" in self.cors_origin_list:
            issues.append("CORS_ORIGINS must not be '*' in production")
        # R3-12: catch the specific dishonesty of *claiming* India-only
        # residency (DATA_RESIDENCY_ASSERT_INDIA_ONLY=true, which a DPIA or
        # SDF filing may cite) without having recorded which regions that
        # claim is actually about, and of shipping with backups whose
        # encryption posture was never asserted either way. Neither check
        # can verify the claim is TRUE - only that it was not left as an
        # unexamined default while being relied upon.
        # This check is opt-in and a no-op unless a deployment sets
        # DATA_RESIDENCY_ASSERT_INDIA_ONLY=true itself (default false), so it
        # cannot newly block a production startup that never touched these
        # flags - see the module docstring above on BACKUP_ENCRYPTION_ENABLED
        # for why that flag is deliberately NOT part of this fail-closed list.
        if self.DATA_RESIDENCY_ASSERT_INDIA_ONLY and (
            self.DATA_RESIDENCY_PRIMARY_DB_REGION == "unspecified"
            or self.DATA_RESIDENCY_BACKUP_REGION == "unspecified"
        ):
            issues.append(
                "DATA_RESIDENCY_ASSERT_INDIA_ONLY is true but DATA_RESIDENCY_PRIMARY_DB_REGION/"
                "DATA_RESIDENCY_BACKUP_REGION is still 'unspecified' - record the actual region(s) "
                "being asserted"
            )
        return issues


@lru_cache
def get_settings() -> Settings:
    return Settings()
