from __future__ import annotations

from typing import Protocol, Sequence

import httpx

from app.settings import Settings

from .gateway import ModelGatewayError
from .usage_store import ModelUsageStore


class EmbeddingGateway(Protocol):
    model_name: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class BailianEmbeddingGateway:
    """OpenAI-compatible Bailian embedding seam with bounded batches."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.model_name = settings.embedding_model
        self.dimensions = settings.embedding_dimensions
        self.client = client or httpx.Client(timeout=settings.request_timeout_seconds)
        self.usage_store = ModelUsageStore(settings) if settings.database_path is not None else None

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        clean = [text.strip() for text in texts if text.strip()]
        if not clean:
            return []
        if not self.settings.model_api_key:
            raise ModelGatewayError("model_credentials_unavailable")
        output: list[list[float]] = []
        try:
            for offset in range(0, len(clean), 20):
                response = self.client.post(
                    f"{self.settings.model_base_url.rstrip('/')}/embeddings",
                    headers={
                        "Authorization": f"Bearer {self.settings.model_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model_name,
                        "input": clean[offset:offset + 20],
                        "dimensions": self.dimensions,
                        "encoding_format": "float",
                    },
                )
                response.raise_for_status()
                body = response.json()
                rows = sorted(body["data"], key=lambda item: item["index"])
                output.extend([list(map(float, item["embedding"])) for item in rows])
                usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
                if self.usage_store is not None:
                    self.usage_store.record(
                        provider="bailian",
                        model=self.model_name,
                        purpose="knowledge_embedding",
                        operation_kind="embedding",
                        input_units=int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0),
                        unit_type="tokens",
                        status="succeeded",
                        metadata={"text_count": len(clean[offset:offset + 20])},
                    )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ModelGatewayError(type(exc).__name__) from exc
        if len(output) != len(clean):
            raise ModelGatewayError("embedding_count_mismatch")
        return output
