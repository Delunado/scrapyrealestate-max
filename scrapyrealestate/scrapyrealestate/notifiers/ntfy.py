"""ntfy JSON publishing through the provider-neutral notifier boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import requests

from scrapyrealestate.domain.notification import NotificationEvent
from scrapyrealestate.notifiers.base import DeliveryResult, NotifierConfigurationError
from scrapyrealestate.notifiers.formatting import format_notification


_TOPIC = re.compile(r"^[A-Za-z0-9_-]+$")


class NtfyHttpClient(Protocol):
    def post(self, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class NtfyConfig:
    server_url: str
    topic: str
    access_token: str | None = field(default=None, repr=False)
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        server_url = _server_url(self.server_url)
        topic = self.topic.strip()
        token = self.access_token.strip() if self.access_token else None
        if not topic or not _TOPIC.fullmatch(topic):
            raise NotifierConfigurationError(
                "ntfy topic must contain only letters, numbers, underscores, or hyphens"
            )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 60
        ):
            raise NotifierConfigurationError(
                "ntfy timeout_seconds must be between 0 and 60"
            )
        object.__setattr__(self, "server_url", server_url)
        object.__setattr__(self, "topic", topic)
        object.__setattr__(self, "access_token", token)
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


class NtfyNotifier:
    """Publish a UTF-8 JSON message to a public or self-hosted ntfy server."""

    def __init__(
        self,
        config: NtfyConfig,
        *,
        client: NtfyHttpClient | None = None,
    ) -> None:
        self._config = config
        self._client = client or requests.Session()

    def send(self, event: NotificationEvent) -> DeliveryResult:
        message = format_notification(event)
        headers = {"Accept": "application/json"}
        if self._config.access_token:
            headers["Authorization"] = f"Bearer {self._config.access_token}"
        try:
            response = self._client.post(
                self._config.server_url,
                json={
                    "topic": self._config.topic,
                    "title": message.subject,
                    "message": message.text,
                },
                headers=headers,
                timeout=self._config.timeout_seconds,
            )
        except requests.Timeout:
            return DeliveryResult.failed("timeout", "ntfy request timed out")
        except (requests.RequestException, OSError):
            return DeliveryResult.failed("transport_error", "ntfy request failed")
        except Exception:
            return DeliveryResult.failed("provider_error", "ntfy delivery failed")

        status_code = getattr(response, "status_code", 0)
        if not 200 <= status_code < 300:
            return DeliveryResult.failed(
                "provider_error", f"ntfy returned HTTP {status_code or 'unknown'}"
            )
        return DeliveryResult.delivered(_response_message_id(response))


def _server_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise NotifierConfigurationError("ntfy server_url is not valid") from error
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise NotifierConfigurationError("ntfy server_url must be absolute HTTP(S)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise NotifierConfigurationError(
            "ntfy server_url must not contain credentials, a query, or a fragment"
        )
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (parsed.scheme.lower(), port) in {("http", 80), ("https", 443)}
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


def _response_message_id(response: Any) -> str | None:
    try:
        value = response.json().get("id")
    except (AttributeError, TypeError, ValueError):
        return None
    return str(value) if value is not None else None
