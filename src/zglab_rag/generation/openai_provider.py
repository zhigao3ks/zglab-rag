from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from time import perf_counter

from pydantic import BaseModel, Field, model_validator

from zglab_rag.generation.contracts import (
    GenerationRequest,
    ProviderResponse,
    ProviderUsage,
)
from zglab_rag.generation.errors import ProviderFailure

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class OpenAICompatibleConfig(BaseModel):
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    model: str = Field(min_length=1)
    timeout_seconds: float = Field(default=60.0, gt=0)
    network_retries: int = Field(default=1, ge=0, le=3)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    @model_validator(mode="after")
    def normalize_base_url(self) -> OpenAICompatibleConfig:
        self.base_url = self.base_url.rstrip("/")
        return self


class OpenAICompatibleProvider:
    """OpenAI-compatible chat completions provider over plain HTTP.

    Business code depends only on the GenerationProvider protocol; network
    retries for transient failures stay inside this layer and never mix with
    the semantic repair loop. The API key is never logged or rendered.
    """

    name = "openai-compatible"

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config

    @property
    def model(self) -> str:
        return self.config.model

    def generate(self, request: GenerationRequest) -> ProviderResponse:
        user_prompt = request.user_prompt
        if request.repair_feedback:
            user_prompt = (
                f"{user_prompt}\n\n"
                "REPAIR FEEDBACK（上一次输出违反了规则，请修正）\n"
                f"{request.repair_feedback}"
            )
        payload = json.dumps(
            {
                "model": self.config.model,
                "temperature": self.config.temperature,
                "messages": [
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
        ).encode("utf-8")
        url = f"{self.config.base_url}/chat/completions"
        http_request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = perf_counter()
        body = self._request_with_retries(http_request)
        latency_ms = (perf_counter() - started) * 1000
        try:
            data = json.loads(body)
            text = data["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ProviderFailure(f"provider returned an unreadable response: {exc}") from exc
        usage_data = data.get("usage") if isinstance(data, dict) else None
        usage = ProviderUsage(
            input_tokens=usage_data.get("prompt_tokens") if usage_data else None,
            output_tokens=usage_data.get("completion_tokens") if usage_data else None,
        )
        return ProviderResponse(
            provider=self.name,
            model=self.config.model,
            text=text,
            latency_ms=latency_ms,
            usage=usage,
        )

    def _request_with_retries(self, http_request: urllib.request.Request) -> bytes:
        attempts = self.config.network_retries + 1
        last_error = ""
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(http_request, timeout=self.config.timeout_seconds) as (
                    response
                ):
                    return response.read()
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                if exc.code not in _RETRYABLE_STATUS:
                    raise ProviderFailure(f"generation provider failed: HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = str(exc)
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
        raise ProviderFailure(f"generation provider unreachable: {last_error}")
