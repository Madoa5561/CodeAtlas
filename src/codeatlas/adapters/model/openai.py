"""OpenAI model provider adapter."""

from __future__ import annotations

import httpx

from codeatlas.adapters.model.openai_compatible import OpenAICompatibleModelProvider
from codeatlas.config.settings import OpenAISettings

PROVIDER_ID = "openai"


class OpenAIModelProvider(OpenAICompatibleModelProvider):
    """Provider-neutral port backed by the OpenAI chat completions API."""

    def __init__(
        self,
        settings: OpenAISettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        api_key = settings.api_key.get_secret_value() if settings.api_key is not None else ""
        super().__init__(
            provider_id=PROVIDER_ID,
            model=settings.model,
            base_url=str(settings.base_url),
            api_key=api_key,
            enabled=settings.enabled,
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            retry_backoff_seconds=settings.retry_backoff_seconds,
            transport=transport,
            client=client,
        )

    @classmethod
    def from_settings(
        cls,
        settings: OpenAISettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> OpenAIModelProvider:
        """Build a provider from validated OpenAI settings."""
        return cls(settings, transport=transport, client=client)
