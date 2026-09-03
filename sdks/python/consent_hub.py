"""
Consent360 Python SDK
=====================

Client library for the Consent360 Consent Management Platform integration APIs.

Installation:
    pip install requests

Usage:
    from consent_hub import Consent360Client

    client = Consent360Client(
        base_url="http://localhost:8000",
        api_key="your-integration-api-key",
    )

    result = client.create_customer_context(
        name="John Doe",
        email="john@example.com",
    )
    print(result["context_token"])
"""

from __future__ import annotations

import requests
from typing import Any, Optional


class Consent360Error(Exception):
    """Base exception for Consent360 SDK errors."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"[{status_code}] {message}")


class Consent360Client:
    """Client for the Consent360 integration APIs.

    Args:
        base_url: The Consent360 backend URL (e.g., ``http://localhost:8000``).
        api_key: Integration API key for server-to-server authentication.
        timeout: Request timeout in seconds (default: 30).
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", self.timeout)
        resp = self._session.request(method, url, **kwargs)
        if not resp.ok:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise Consent360Error(resp.status_code, detail)
        return resp.json()

    # ------------------------------------------------------------------
    # Integration APIs (server-to-server, X-API-Key auth)
    # ------------------------------------------------------------------

    def create_customer_context(
        self,
        name: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        customer_id: Optional[str] = None,
        source_app: str = "EXTERNAL_APP",
        callback_url: Optional[str] = None,
    ) -> dict:
        """Identify or create a customer and mint a short-lived context token.

        The context token can be used to open the consent portal for the
        customer to manage their consents.

        Args:
            name: Customer's display name.
            email: Customer email (used for identity lookup).
            phone: Customer phone (fallback for identity derivation).
            customer_id: Explicit external customer ID.
            source_app: Identifier of the calling application.
            callback_url: Optional callback URL.

        Returns:
            Dict with keys: ``context_token``, ``context_id``,
            ``customer_id``, ``name``, ``expires_in_minutes``,
            ``source_app``, ``ui_url``, ``request_id``.
        """
        payload: dict[str, Any] = {"name": name, "source_app": source_app}
        if email:
            payload["email"] = email
        if phone:
            payload["phone"] = phone
        if customer_id:
            payload["customer_id"] = customer_id
        if callback_url:
            payload["callback_url"] = callback_url
        return self._request("POST", "/consent/customer-context", json=payload)

    def get_context_status(self, context_token: str) -> dict:
        """Check the status of a context token.

        Returns:
            Dict with key ``message``: ``"VALID"``, ``"CONSUMED"``, or
            ``"EXPIRED"``.

        Note:
            Requires a staff JWT bearer token with ``context.use``
            permission. For server-to-server calls, use the admin SDK
            with a bearer token instead.
        """
        return self._request(
            "GET",
            f"/consent/context/status/{context_token}",
        )

    # ------------------------------------------------------------------
    # Portal APIs (customer self-service, context token auth)
    # ------------------------------------------------------------------

    def get_portal_overview(self, context_token: str) -> dict:
        """Get the customer's consent overview (all purposes + statuses).

        Args:
            context_token: The JWT context token.

        Returns:
            Dict with keys: ``customer``, ``purposes`` (list of purpose
            objects with consent statuses).
        """
        return self._request(
            "GET",
            "/portal/overview",
            headers={"X-Context-Token": context_token},
        )

    def grant_consent(self, context_token: str, purpose_code: str) -> dict:
        """Grant all consents for a specific purpose.

        Args:
            context_token: The JWT context token.
            purpose_code: Purpose code (e.g., ``"functional"``).

        Returns:
            Dict with keys: ``purpose_code``, ``action``, ``affected``,
            ``message``.
        """
        return self._request(
            "POST",
            "/portal/grant",
            json={"purpose_code": purpose_code},
            headers={"X-Context-Token": context_token},
        )

    def withdraw_consent(self, context_token: str, purpose_code: str) -> dict:
        """Withdraw all consents for a specific purpose.

        Args:
            context_token: The JWT context token.
            purpose_code: Purpose code (e.g., ``"analytics"``).

        Returns:
            Dict with keys: ``purpose_code``, ``action``, ``affected``,
            ``message``.
        """
        return self._request(
            "POST",
            "/portal/withdraw",
            json={"purpose_code": purpose_code},
            headers={"X-Context-Token": context_token},
        )

    # ------------------------------------------------------------------
    # CRM Consent APIs (cookie preferences)
    # ------------------------------------------------------------------

    def get_consent_preferences(self, customer_id: int) -> dict:
        """Get saved cookie-category preferences for a CRM customer.

        Args:
            customer_id: CRM customer database ID.

        Returns:
            Dict with key ``preferences`` containing the saved choices.
        """
        return self._request(
            "GET",
            f"/crm/customers/{customer_id}/consent-preferences",
        )

    def save_consent_preferences(
        self,
        customer_id: int,
        categories: dict[str, bool],
        lang: str = "en",
    ) -> dict:
        """Save cookie-category preferences for a CRM customer.

        This mirrors the choices onto consent platform consent records
        (grant/activate or withdraw per purpose).

        Args:
            customer_id: CRM customer database ID.
            categories: Dict mapping category names to booleans.
                Keys: ``"necessary"``, ``"functional"``, ``"analytics"``,
                ``"advertising"``.
            lang: Language preference (default: ``"en"``).

        Returns:
            Dict with keys: ``saved``, ``preferences``.
        """
        return self._request(
            "PUT",
            f"/crm/customers/{customer_id}/consent-preferences",
            json={"lang": lang, "categories": categories},
        )

    def create_crm_consent_context(self, customer_id: int) -> dict:
        """Mint a consent context token for an existing CRM customer.

        Args:
            customer_id: CRM customer database ID.

        Returns:
            Dict with keys: ``customer``, ``created``, ``context_token``,
            ``consent_portal_url``, ``expires_in_minutes``.
        """
        return self._request(
            "POST",
            f"/crm/customers/{customer_id}/consent-context",
        )

    # ------------------------------------------------------------------
    # Customer Purge API (server-to-server, X-API-Key auth)
    # ------------------------------------------------------------------

    def delete_customer_by_email(self, email: str) -> dict:
        """Purge a customer's entire consent profile.

        Deletes all consent records, history, evidence, contexts, and
        audit entries for the customer identified by email.

        Args:
            email: The customer's email address.

        Returns:
            Dict with keys: ``deleted`` (bool), ``external_id`` (str).
        """
        return self._request(
            "DELETE",
            f"/crm/customers/by-email/{email}",
        )
