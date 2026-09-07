"""TOTP-based MFA for staff accounts (R3-02/R-02/H-02: "MFA for staff").

Persistence gap, read before wiring MFA_ENFORCED=True
------------------------------------------------------
Enrollment state (the TOTP secret, whether MFA is enabled, backup codes) is
held by `InMemoryMfaStore` below, in process memory only. This is a
deliberate, clearly-flagged placeholder, not a shortcut: a real MFA secret
needs its own encrypted, persistent home - `users.mfa_secret`
(EncryptedString), `users.mfa_enabled` (Boolean), `users.mfa_enrolled_at`
(DateTime) and `users.mfa_backup_codes` (EncryptedJSON) on the `users`
table - and `app/models/entities.py` is outside this lane's file scope
(another lane owns the schema/migration chain concurrently). See the
lane report for the exact columns to add and the migration this needs.

Because of that gap, `MFA_ENFORCED` (app/core/config.py) is not merely
defaulted to `False` - `Settings._reject_unhonoured_flags` REFUSES TO
CONSTRUCT SETTINGS AT ALL if it is set, in every environment. It was
previously read by no code anywhere, so an operator could set it, see no
error, and believe MFA was mandatory for staff when nothing had changed.
Until the columns above land and this module is rewired to read/write
them, failing loudly at startup is the only honest behaviour: enforcing
MFA against a store that forgets every enrollment on restart would lock
every staff account out on the next deploy. Meanwhile MFA remains
available as an opt-in second factor
(`/auth/mfa/enroll` -> `/auth/mfa/confirm` -> required at `/auth/mfa/verify-login`
for whichever user chose to enable it) that survives for the lifetime of
one running process - enough to prove the TOTP mechanics end-to-end and to
demonstrate the login flow, but an operator should treat every staff
member as needing to re-enroll after a restart until the persistent store
lands.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pyotp

ISSUER = "Consent360"


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str, issuer: str = ISSUER) -> str:
    """otpauth:// URI an authenticator app (Google Authenticator, Authy,
    1Password, ...) can scan as a QR code to enroll this secret."""
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def verify_code(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=1)  # tolerate +-30s clock drift
    except Exception:
        return False


def generate_backup_codes(count: int = 8) -> list[str]:
    return [secrets.token_hex(4) for _ in range(count)]


@dataclass
class MfaState:
    secret: str | None = None
    enabled: bool = False
    backup_codes: list[str] = field(default_factory=list)
    enrolled_at: datetime | None = None


class InMemoryMfaStore:
    """Process-local MFA enrollment store - see the module docstring for
    why, and what would replace it once schema changes are sequenced."""

    def __init__(self) -> None:
        self._by_user_id: dict[int, MfaState] = {}

    def get(self, user_id: int) -> MfaState:
        return self._by_user_id.setdefault(user_id, MfaState())

    def start_enrollment(self, user_id: int) -> str:
        state = self.get(user_id)
        state.secret = generate_secret()
        state.enabled = False
        state.backup_codes = []
        return state.secret

    def confirm_enrollment(self, user_id: int, code: str) -> list[str] | None:
        """Verifies `code` against the pending secret; on success, enables
        MFA and returns a fresh set of one-time backup codes. Returns None
        on failure (no pending secret, or the code doesn't verify)."""
        state = self.get(user_id)
        if not state.secret or not verify_code(state.secret, code):
            return None
        state.enabled = True
        state.enrolled_at = datetime.now(timezone.utc)
        state.backup_codes = generate_backup_codes()
        return list(state.backup_codes)

    def disable(self, user_id: int) -> None:
        self._by_user_id[user_id] = MfaState()

    def is_enabled(self, user_id: int) -> bool:
        return self.get(user_id).enabled

    def verify(self, user_id: int, code: str) -> bool:
        state = self.get(user_id)
        if not state.enabled or not state.secret:
            return False
        if verify_code(state.secret, code):
            return True
        if code in state.backup_codes:
            state.backup_codes.remove(code)  # one-time use
            return True
        return False

    def reset_for_tests(self) -> None:
        self._by_user_id.clear()


mfa_store = InMemoryMfaStore()
