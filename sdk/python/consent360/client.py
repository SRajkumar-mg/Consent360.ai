"""Consent360 SDK — main client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from consent360.exceptions import (
    AuthError,
    Consent360Error,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from consent360.models import (
    ContextStatus,
    CustomerContext,
    PortalAction,
    PortalOverview,
    PurgeResult,
)


class Consent360:
    """Client for the Consent360 integration APIs.

    Args:
        api_key: Integration API key (X-API-Key header).
        base_url: Consent360 backend URL (e.g. "http://localhost:8000").
        timeout: Request timeout in seconds.

    Example::

        from consent360 import Consent360

        client = Consent360(api_key="your-api-key", base_url="http://localhost:8000")
        ctx = client.create_customer_context(name="John", email="john@example.com")
        print(ctx.ui_url)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost:8000",
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Integration APIs (X-API-Key protected)
    # ------------------------------------------------------------------

    def create_customer_context(
        self,
        name: str,
        email: str | None = None,
        phone: str | None = None,
        customer_id: str | None = None,
        source_app: str | None = None,
    ) -> CustomerContext:
        """Create or look up a customer and mint a short-lived context token.

        Args:
            name: Customer name (required).
            email: Customer email (used for dedup).
            phone: Customer phone (used for dedup).
            customer_id: External ID from your system (optional).
            source_app: Source application identifier.

        Returns:
            CustomerContext with token, UI URL, and customer details.
        """
        payload: dict[str, Any] = {"name": name}
        if email is not None:
            payload["email"] = email
        if phone is not None:
            payload["phone"] = phone
        if customer_id is not None:
            payload["customer_id"] = customer_id
        if source_app is not None:
            payload["source_app"] = source_app

        data = self._request("POST", "/consent/customer-context", json_body=payload)
        return CustomerContext.from_dict(data)

    def get_context_status(self, context_token: str) -> ContextStatus:
        """Check whether a context token is valid, consumed, or expired.

        Args:
            context_token: The JWT context token.

        Returns:
            ContextStatus with status string (VALID | CONSUMED | EXPIRED).
        """
        data = self._request("GET", f"/consent/context/status/{context_token}")
        return ContextStatus.from_dict(data)

    def purge_customer(self, email: str) -> PurgeResult:
        """Delete all consent-platform records for a customer.

        Args:
            email: Customer email address.

        Returns:
            PurgeResult with deletion status.
        """
        encoded = urllib.request.quote(email, safe="")
        data = self._request("DELETE", f"/crm/customers/by-email/{encoded}")
        return PurgeResult.from_dict(data)

    # ------------------------------------------------------------------
    # Portal APIs (X-Context-Token protected)
    # ------------------------------------------------------------------

    def get_portal_overview(self, context_token: str) -> PortalOverview:
        """Get all purposes and consent status for a customer.

        Args:
            context_token: The customer's context token.

        Returns:
            PortalOverview with purposes and their grant status.
        """
        data = self._request(
            "GET",
            "/portal/overview",
            headers={"X-Context-Token": context_token},
        )
        return PortalOverview.from_dict(data)

    def grant_consent(self, context_token: str, purpose_code: str) -> PortalAction:
        """Grant consent for a specific purpose.

        Args:
            context_token: The customer's context token.
            purpose_code: Purpose code (e.g. "analytics", "advertising").

        Returns:
            PortalAction with affected count and message.
        """
        data = self._request(
            "POST",
            "/portal/grant",
            json_body={"purpose_code": purpose_code},
            headers={"X-Context-Token": context_token},
        )
        return PortalAction.from_dict(data)

    def withdraw_consent(self, context_token: str, purpose_code: str) -> PortalAction:
        """Withdraw consent for a specific purpose.

        Args:
            context_token: The customer's context token.
            purpose_code: Purpose code (e.g. "analytics", "advertising").

        Returns:
            PortalAction with affected count and message.
        """
        data = self._request(
            "POST",
            "/portal/withdraw",
            json_body={"purpose_code": purpose_code},
            headers={"X-Context-Token": context_token},
        )
        return PortalAction.from_dict(data)

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        req_headers: dict[str, str] = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
        }
        if headers:
            req_headers.update(headers)

        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            req_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=req_headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                err_body = json.loads(exc.read().decode("utf-8"))
                detail = err_body.get("detail", str(err_body))
            except Exception:
                detail = str(exc)

            status = exc.code
            if status == 401:
                raise AuthError(f"Authentication failed: {detail}", status_code=status, detail=detail)
            elif status == 404:
                raise NotFoundError(f"Not found: {detail}", status_code=status, detail=detail)
            elif status == 422:
                raise ValidationError(f"Validation error: {detail}", status_code=status, detail=detail)
            elif status == 429:
                raise RateLimitError(f"Rate limit exceeded: {detail}", status_code=status, detail=detail)
            else:
                raise Consent360Error(f"HTTP {status}: {detail}", status_code=status, detail=detail)
        except urllib.error.URLError as exc:
            raise Consent360Error(f"Connection error: {exc.reason}")
