"""Versioned generic HTTP webhook notifications."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import requests

from scrapyrealestate.domain.notification import NotificationEvent
from scrapyrealestate.notifiers.base import DeliveryResult, NotifierConfigurationError
from scrapyrealestate.notifiers.formatting import format_notification


WEBHOOK_SCHEMA_VERSION = "1.0"


class WebhookHttpClient(Protocol):
    def post(self, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class WebhookConfig:
    endpoint_url: str
    authorization: str | None = field(default=None, repr=False)
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        endpoint_url = _endpoint_url(self.endpoint_url)
        authorization = self.authorization.strip() if self.authorization else None
        if authorization and ("\r" in authorization or "\n" in authorization):
            raise NotifierConfigurationError(
                "webhook authorization must be a single HTTP header value"
            )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 60
        ):
            raise NotifierConfigurationError(
                "webhook timeout_seconds must be between 0 and 60"
            )
        object.__setattr__(self, "endpoint_url", endpoint_url)
        object.__setattr__(self, "authorization", authorization)
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


class WebhookNotifier:
    def __init__(
        self,
        config: WebhookConfig,
        *,
        client: WebhookHttpClient | None = None,
    ) -> None:
        self._config = config
        self._client = client or requests.Session()

    def send(self, event: NotificationEvent) -> DeliveryResult:
        headers = {"Accept": "application/json"}
        if self._config.authorization:
            headers["Authorization"] = self._config.authorization
        try:
            response = self._client.post(
                self._config.endpoint_url,
                json=build_webhook_payload(event),
                headers=headers,
                timeout=self._config.timeout_seconds,
            )
        except requests.Timeout:
            return DeliveryResult.failed("timeout", "webhook request timed out")
        except (requests.RequestException, OSError):
            return DeliveryResult.failed("transport_error", "webhook request failed")
        except Exception:
            return DeliveryResult.failed("provider_error", "webhook delivery failed")

        status_code = getattr(response, "status_code", 0)
        if not 200 <= status_code < 300:
            return DeliveryResult.failed(
                "provider_error", f"webhook returned HTTP {status_code or 'unknown'}"
            )
        request_id = getattr(response, "headers", {}).get("X-Request-ID")
        return DeliveryResult.delivered(str(request_id) if request_id else None)


def build_webhook_payload(event: NotificationEvent) -> dict[str, Any]:
    message = format_notification(event)
    return {
        "schema_version": WEBHOOK_SCHEMA_VERSION,
        "event": {
            "id": event.id,
            "type": event.event_type.value,
            "occurred_at": event.occurred_at.isoformat().replace("+00:00", "Z"),
        },
        "search": {"id": event.search_id, "name": event.search_name},
        "listing": {
            "id": event.listing_id,
            "title": event.listing_title,
            "portal": event.portal.value if event.portal else None,
            "url": event.canonical_url,
            "price_euros": event.price_euros,
            "previous_price_euros": event.previous_price_euros,
            "area_sqm": event.area_sqm,
            "rooms": event.rooms,
            "location": event.location,
            "neighbourhood": event.neighbourhood,
        },
        "message": {"subject": message.subject, "text": message.text},
    }


def _endpoint_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise NotifierConfigurationError("webhook endpoint_url is not valid") from error
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise NotifierConfigurationError(
            "webhook endpoint_url must be absolute HTTP(S)"
        )
    if parsed.username or parsed.password or parsed.fragment:
        raise NotifierConfigurationError(
            "webhook endpoint_url must not contain credentials or a fragment"
        )
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (parsed.scheme.lower(), port) in {("http", 80), ("https", 443)}
    netloc = host if port is None or default_port else f"{host}:{port}"
    return urlunsplit(
        (parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, "")
    )
