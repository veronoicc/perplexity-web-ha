"""Perplexity Web API client."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiohttp

API_VERSION = "2.18"
CLIENT_USER_AGENT = "Perplexity/641 CFNetwork/1568 Darwin/25.2.0"
API_CLIENT = "default"


class PerplexityError(Exception):
    """Base exception for Perplexity Web errors."""


class PerplexityAuthError(PerplexityError):
    """Authentication failure."""


class PerplexityApiError(PerplexityError):
    """API request failure."""


@dataclass
class AskResult:
    """Result of an ask query."""

    text: str
    sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SearchConfigItem:
    """Model configuration item."""

    label: str
    description: str
    subscription_tier: str | None = None
    non_reasoning_model: str | None = None
    reasoning_model: str | None = None

    def best_model(self, use_reasoning: bool = False) -> str | None:
        """Return the best model ID based on reasoning preference."""
        if use_reasoning:
            return self.reasoning_model or self.non_reasoning_model
        return self.non_reasoning_model or self.reasoning_model


class AuthClient:
    """Perplexity authentication client."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        """Initialize auth client."""
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create client session."""
        if self._session is None or getattr(self._session, "closed", False) is True:
            self._session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar())
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close session if owned."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> AuthClient:
        """Enter context manager."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit context manager."""
        await self.close()

    def _headers(self) -> dict[str, str]:
        """Base headers for auth requests."""
        return {
            "User-Agent": CLIENT_USER_AGENT,
            "X-App-ApiVersion": API_VERSION,
        }

    async def get_csrf_token(self) -> str:
        """Fetch the initial CSRF token."""
        session = await self._get_session()
        url = "https://www.perplexity.ai/api/auth/csrf"
        try:
            async with session.get(url, headers=self._headers()) as resp:
                if resp.status != 200:
                    raise PerplexityAuthError(
                        f"Failed to fetch CSRF token: HTTP {resp.status}"
                    )
                data = await resp.json()
                token = data.get("csrfToken")
                if not token:
                    raise PerplexityAuthError("Missing csrfToken in response")
                return token
        except aiohttp.ClientError as err:
            raise PerplexityAuthError(
                f"Network error fetching CSRF token: {err}"
            ) from err

    async def send_email_otp(self, email: str, csrf_token: str) -> None:
        """Send a one-time password to the target email address."""
        session = await self._get_session()
        url = "https://www.perplexity.ai/api/auth/signin-email"
        payload = {"email": email, "csrfToken": csrf_token}
        try:
            async with session.post(url, headers=self._headers(), json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise PerplexityAuthError(
                        f"Failed to send email OTP: HTTP {resp.status} - {text}"
                    )
        except aiohttp.ClientError as err:
            raise PerplexityAuthError(
                f"Network error sending email OTP: {err}"
            ) from err

    async def verify_otp(self, email: str, otp: str, csrf_token: str) -> str:
        """Submit the OTP and extract the session token."""
        session = await self._get_session()
        url = "https://www.perplexity.ai/api/auth/signin-otp"
        payload = {
            "email": email,
            "otp": otp,
            "csrfToken": csrf_token,
        }
        try:
            async with session.post(url, headers=self._headers(), json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise PerplexityAuthError(
                        f"Failed to verify OTP: HTTP {resp.status} - {text}"
                    )

                # Check cookies on response or cookie jar
                cookie_name = "__Secure-next-auth.session-token"
                if cookie_name in resp.cookies:
                    return resp.cookies[cookie_name].value

                for cookie in session.cookie_jar:
                    if cookie.key == cookie_name:
                        return cookie.value

                # Check JSON payload fallback
                try:
                    data = await resp.json()
                    if isinstance(data, dict) and data.get("token"):
                        return str(data["token"])
                except Exception:
                    pass

                raise PerplexityAuthError(
                    "Could not extract session token from signin response"
                )
        except aiohttp.ClientError as err:
            raise PerplexityAuthError(f"Network error verifying OTP: {err}") from err


class PerplexityClient:
    """Authenticated Perplexity Web API client."""

    def __init__(self, session: aiohttp.ClientSession, session_token: str) -> None:
        """Initialize Perplexity client."""
        self._session = session
        self.session_token = session_token

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Common headers for authenticated requests."""
        headers = {
            "Cookie": f"__Secure-next-auth.session-token={self.session_token}",
            "User-Agent": CLIENT_USER_AGENT,
            "X-App-ApiClient": API_CLIENT,
            "X-App-ApiVersion": API_VERSION,
        }
        if extra:
            headers.update(extra)
        return headers

    async def get_user_profile(self) -> dict[str, Any]:
        """Fetch the authenticated user's profile."""
        url = f"https://www.perplexity.ai/api/auth/session?version={API_VERSION}&source={API_CLIENT}"
        try:
            async with self._session.get(url, headers=self._headers()) as resp:
                if resp.status != 200:
                    raise PerplexityApiError(
                        f"Failed to fetch user profile: HTTP {resp.status}"
                    )
                data = await resp.json()
                user = data.get("user")
                if not user:
                    raise PerplexityApiError("User profile missing in session response")
                return user
        except aiohttp.ClientError as err:
            raise PerplexityApiError(
                f"Network error fetching user profile: {err}"
            ) from err

    async def get_default_models(self) -> dict[str, str]:
        """Fetch the mapping of application modes to default models."""
        url = f"https://www.perplexity.ai/rest/models/config/v2?version={API_VERSION}&source={API_CLIENT}"
        try:
            async with self._session.get(url, headers=self._headers()) as resp:
                if resp.status != 200:
                    raise PerplexityApiError(
                        f"Failed to fetch models config: HTTP {resp.status}"
                    )
                data = await resp.json()
                return data.get("default_models") or {}
        except aiohttp.ClientError as err:
            raise PerplexityApiError(
                f"Network error fetching default models: {err}"
            ) from err

    async def get_available_models(self) -> list[SearchConfigItem]:
        """Retrieve models available to the user based on subscription tier."""
        try:
            profile = await self.get_user_profile()
            user_tier = (profile.get("subscription_tier") or "free").lower()
        except Exception:
            user_tier = "free"

        url = f"https://www.perplexity.ai/rest/models/config/v2?version={API_VERSION}&source={API_CLIENT}"
        try:
            async with self._session.get(url, headers=self._headers()) as resp:
                if resp.status != 200:
                    raise PerplexityApiError(
                        f"Failed to fetch models config: HTTP {resp.status}"
                    )
                data = await resp.json()
        except aiohttp.ClientError as err:
            raise PerplexityApiError(
                f"Network error fetching models config: {err}"
            ) from err

        search_config = data.get("search_config") or []
        allowed: list[SearchConfigItem] = []

        for item in search_config:
            req_tier = (item.get("subscription_tier") or "free").lower()
            is_allowed = False
            if user_tier == "max":
                is_allowed = True
            elif user_tier == "pro":
                is_allowed = req_tier in ("free", "pro")
            else:
                is_allowed = req_tier in ("free", user_tier)

            if is_allowed:
                allowed.append(
                    SearchConfigItem(
                        label=item.get("label", ""),
                        description=item.get("description", ""),
                        subscription_tier=item.get("subscription_tier"),
                        non_reasoning_model=item.get("non_reasoning_model"),
                        reasoning_model=item.get("reasoning_model"),
                    )
                )

        return allowed

    async def ask_complete(
        self, query_str: str, params: dict[str, Any] | None = None
    ) -> AskResult:
        """Execute a query and collect the complete SSE stream response."""
        params = params or {}
        payload_params = {
            "attachments": params.get("attachments", []),
            "search_focus": params.get("search_focus", "internet"),
            "mode": params.get("mode", "concise"),
            "model_preference": params.get("model_preference", "experimental"),
            "sources": params.get("sources", ["web"]),
            "version": API_VERSION,
            "use_schematized_api": True,
            "skip_search_enabled": False,
            "always_search_override": True,
            "is_incognito": params.get("is_incognito", True),
        }

        request_id = str(uuid.uuid4())
        extra_headers = {
            "X-Request-ID": request_id,
            "X-Perplexity-Request-Reason": "submit",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

        url = "https://www.perplexity.ai/rest/sse/perplexity_ask"
        body = {
            "query_str": query_str,
            "params": payload_params,
        }

        try:
            async with self._session.post(
                url,
                headers=self._headers(extra_headers),
                json=body,
            ) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    raise PerplexityApiError(
                        f"Ask request failed with HTTP {resp.status}: {err_text}"
                    )

                full_text: list[str] = []
                final_sources: list[dict[str, Any]] = []
                next_chunk_offset = 0

                async for line in resp.content:
                    line_str = line.decode("utf-8", errors="replace").strip()
                    if not line_str.startswith("data:"):
                        continue
                    data_str = line_str[5:].strip()
                    if data_str == "[DONE]":
                        break
                    if not data_str:
                        continue

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    blocks = event.get("blocks") or []
                    for block in blocks:
                        if md := block.get("markdown_block"):
                            chunks = md.get("chunks") or []
                            start_offset = md.get("chunk_starting_offset", 0)
                            for i, chunk in enumerate(chunks):
                                global_idx = start_offset + i
                                if global_idx >= next_chunk_offset:
                                    full_text.append(chunk)
                                    next_chunk_offset = global_idx + 1

                        if wb := block.get("web_result_block"):
                            if web_results := wb.get("web_results"):
                                final_sources = web_results

                return AskResult(text="".join(full_text), sources=final_sources)
        except aiohttp.ClientError as err:
            raise PerplexityApiError(
                f"Network error during ask_complete: {err}"
            ) from err
