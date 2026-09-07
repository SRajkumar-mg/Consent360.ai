# Security Scan Findings (R3-12)

Tooling: `pip-audit` 2.10.1 (dependency CVEs) and `bandit` 1.9.4 (static analysis), run against
`cms/backend` on 2026-09-04. Both are wired into `.github/scripts/backend_ci_local.sh` and
`.github/workflows/backend-ci.yml` so this is a repeatable check, not a one-off. Neither tool was
previously run against this codebase as part of this task series — this is the first pass.

Re-run: `.github/scripts/backend_ci_local.sh` (steps 1–2), or individually:
```bash
cd cms/backend
python -m pip_audit -r requirements.txt --desc
python -m bandit -r app -ll
```

## Dependency vulnerabilities (pip-audit)

### Fixed in this task (trivial, patch-level, verified compatible)

| Package | Was | Now | Why safe to bump | Findings resolved |
|---|---|---|---|---|
| `python-jose` | 3.3.0 | 3.4.0 | Patch release; used only for HS256 JWT encode/decode (`app/core/security.py`) — no JWE, no ECDSA, so both fixed CVEs' actual attack surface (JWE "JWT bomb" decompression DoS, OpenSSH-key algorithm confusion) wasn't reachable either way, but the fix is free and removes the class entirely. Full test suite re-run and confirmed passing after the bump (see note below). | PYSEC-2024-232, PYSEC-2024-233, PYSEC-2025-185 |
| `python-multipart` | 0.0.20 | 0.0.31 | Confirmed **zero** usage of `Form(...)`/`File(...)`/`UploadFile` anywhere in `app/api/routes/*.py` (grepped) — this app has no file-upload or form-encoded endpoint, so none of the fixed CVEs (path traversal via `UPLOAD_DIR`, multipart/urlencoded parsing DoS, HTTP parameter pollution) were exploitable in this codebase's actual usage. Bumped anyway since it's free and FastAPI still imports the package. | PYSEC-2026-1852, -3038, -3037, -3036, -3040, -3039 |

*(Note: exact current pass count fluctuated during this task due to concurrent, uncommitted work from
other agents on the same branch — see the R3-12 handoff report's test section for the honest accounting.
The dependency bump itself was verified in isolation before other agents' changes landed.)*

### Reviewed, NOT fixed (non-trivial — needs a coordinated follow-up, not a silent patch bump)

| Package | Installed | Fix requires | Why deferred | Severity / exploitability here |
|---|---|---|---|---|
| `starlette` | 0.41.3 | 1.0.1–1.3.1 across the several CVEs (i.e. a `starlette` major-version jump) | Transitive via `fastapi==0.115.6`, which pins a compatible `starlette` range; bumping `starlette` alone would desync from FastAPI, and bumping FastAPI to a version supporting `starlette` 1.x is itself a framework major-version upgrade touching every route in the app — squarely out of this lane's file boundary (routes/main.py owned by other lanes) and too high-risk to do unreviewed. | Confirmed **not exploitable via the specific CVEs found**: this app has zero `StaticFiles`/`FileResponse`/`UploadFile` usage (grepped `app/`), so the Range-header ReDoS and Windows-UNC-path SSRF findings don't apply. The Host-header/path-authority-confusion findings (PYSEC-2026-161, -248, -249) matter only to code that trusts `request.url`/`request.url.path` for authorization instead of `scope["path"]` — this app's routing and permission checks (`app/api/deps.py::require_permission`) never read `request.url` for auth decisions, so these are also not reachable as far as this codebase's own logic goes, but the finding should still be tracked since a future middleware could introduce that pattern. **Recommend:** a dedicated task to bump FastAPI/Starlette together with a full route re-test, not bundled into R3-12. |
| `langchain-core` | 0.3.86 | 1.2.11 / 1.2.22 (a 0.x → 1.x major bump) | Transitive via `langchain-groq==0.2.1`, which was written against the 0.x API; bumping `langchain-core` alone without confirming `langchain-groq` compatibility risks breaking the chatbot route entirely. | **Not exploitable in this codebase's usage**: the two CVEs are (a) path traversal in `langchain_core.prompts.loading.load_prompt()` — never called anywhere in `chatbot.py`, which builds its system prompt via an f-string, not a loaded prompt file — and (b) SSRF in `ChatOpenAI.get_num_tokens_from_messages()`'s image-URL fetching — this app uses `ChatGroq`, not `ChatOpenAI`, and never sends image content blocks. **Recommend:** bump `langchain-groq` to a `langchain-core>=1.x`-compatible release as a dedicated, tested change, since `chatbot.py`'s prompt-injection/redaction logic (see `ALGORITHM_REGISTER.md`) should be re-verified after any langchain upgrade. |
| `pytest` | 8.3.4 | 9.0.3 (major bump) | Dev-only dependency, never shipped to production; a major-version bump risks breaking plugin compatibility or fixture behaviour across ~300 existing tests maintained by multiple concurrent agents right now — not something to do unreviewed mid-task. | **Low practical severity**: PYSEC-2026-1845 requires a *local* attacker who can already write to `/tmp` on the same host as the test run — not a remote or production concern. **Recommend:** bump in a dedicated, low-traffic window with the full suite re-verified by whoever owns `tests/` at that time. |
| `ecdsa` | 0.19.2 | *(none published — upstream has stated no fix is planned)* | Upstream (`python-ecdsa`) explicitly considers side-channel/timing attacks out of scope and has no planned fix. | **Not reachable in this codebase**: `JWT_ALGORITHM` is hard-coded to `HS256` everywhere (`app/core/config.py`, `app/core/security.py`) — no ECDSA signing or key generation is ever performed; `ecdsa` is pulled in transitively by `python-jose`'s dependency graph but its vulnerable code path (`SigningKey.sign_digest` / ECDH) is never called. **Accepted risk**, tracked here for visibility; re-check if the JWT algorithm is ever changed away from HS256. |

## Static analysis (bandit)

All findings are in files outside this lane's edit boundary (`app/core/*`, `app/api/routes/*`,
`app/services/*` belong to other lanes per the R3-12 handoff) — **reported here, not fixed**, per this
task's brief ("fix the trivial ones, do not silently suppress anything" — none of these are trivial
one-line fixes safe to make without the owning lane's context).

| Location | Rule | Severity / Confidence | Assessment |
|---|---|---|---|
| `app/core/alerting.py:48` | B310 (`urllib.urlopen`) | Medium / High | Already reviewed and annotated in source (`# noqa: S310 - operator-configured, not user input`) — the URL comes from `ALERT_WEBHOOK_URL`, an operator-set config value, never user input. Confirmed by `tests/test_webhooks.py` (new in this task) that delivery failures are swallowed, never raised. **No action needed.** |
| `app/core/encryption.py:498,511,550,569` | B608 (SQL built via f-string) | Medium / Low confidence | `table_name`/`column_name`/`pk_column` in `encrypt_column_in_table`/`rotate_column_key` are **hard-coded call-site arguments from operator-run migration scripts** (`scripts/encrypt_existing_data.py`, `scripts/rotate_encryption_keys.py`), never derived from any request or user input — bandit's low-confidence flag matches its own stated caveat for this pattern. **Recommend** (for whoever owns `app/core/encryption.py`): a `# nosec B608` comment with this exact justification, so future bandit runs don't re-flag it as new. Not fixed here — outside this lane's file boundary. |
| `app/services/otp.py:43` | B311 (`random` module for a security-relevant value) | Low / High confidence | **Real finding, not a false positive.** `_generate_code()` uses `random.randint(0, 999999)` to generate the 6-digit OTP used for identity verification (`start_otp`/`confirm_otp`). Python's `random` is a Mersenne Twister — not cryptographically secure; with enough observed outputs an attacker can in principle predict future codes, undermining the verification-attempt-cap protection built around it. **Recommend:** replace with `secrets.randbelow(1_000_000)` (stdlib, CSPRNG-backed, same output shape). This is a real, MEDIUM-severity-in-practice finding on a security-sensitive code path; not fixed here because `app/services/otp.py` is outside this lane's file boundary — flagging for the owning lane. |
| `app/api/routes/{consents,crm,portal}.py`, `app/core/{access_log,token_revocation}.py` | B112/B110 (try/except/continue or pass) | Low / High confidence | Reviewed individually: each is a deliberate fail-open-to-fallback pattern (e.g. Redis unreachable → fall back to in-memory token revocation store; a bearer token that fails to decode while extracting an actor label for access logging → label it `"unknown-bearer"` rather than crash the logging middleware). None swallow a security *decision* — they swallow best-effort/observability failures. **No action needed.** |
| `app/core/config.py:187` / `app/jobs/context_token_cleanup_job.py:8` | B105 (hardcoded password string) | Low / Medium confidence | False positives: `"change-me"` is the intentionally-obvious default `JWT_SECRET` value that `Settings.production_issues()` explicitly checks for and fails closed on if still present in production (see `tests/test_encryption_hardening.py::test_production_issues_flags_every_insecure_default`); `"sha256:"` is a ciphertext-format prefix marker, not a credential. **No action needed.** |

### Summary

- **2 dependency findings fixed** (patch-level, verified via full test re-run before other agents'
  concurrent changes landed).
- **4 dependency findings reviewed and deferred** with a written exploitability assessment each — none
  are reachable via this codebase's actual usage patterns today, but the two "major version bump
  required" ones (`starlette`/FastAPI, `langchain-core`/`langchain-groq`) are real technical debt worth
  a dedicated task.
- **1 real, actionable static-analysis finding** (`app/services/otp.py`'s use of `random` instead of
  `secrets` for OTP generation) reported to the owning lane — not fixed here, outside this lane's file
  boundary.
- **7 static-analysis findings reviewed as false positives / already-mitigated**, with the specific
  reasoning recorded above so a future scan doesn't need to re-litigate them from scratch.
- **Nothing was silently suppressed** — no `# nosec` or bandit-config exclusion was added anywhere; the
  one place a `# nosec` comment is recommended (`encryption.py`'s B608s) is left for that file's owner
  to add with attribution, not added unilaterally by this lane.
