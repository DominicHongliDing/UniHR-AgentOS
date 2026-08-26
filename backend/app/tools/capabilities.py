from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CapabilityContext:
    data_class: str
    read_only: bool = True


class CapabilityAdapter(Protocol):
    capability: str

    def healthy(self) -> bool: ...


class CapabilityUnavailableError(RuntimeError):
    pass


class CapabilityRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, CapabilityAdapter] = {}

    def register(self, adapter: CapabilityAdapter) -> None:
        self._adapters[adapter.capability] = adapter

    def resolve(self, capability: str, context: CapabilityContext) -> CapabilityAdapter:
        if not context.read_only or context.data_class != "public":
            raise CapabilityUnavailableError("Public capabilities require public, read-only context.")
        adapter = self._adapters.get(capability)
        if adapter is None or not adapter.healthy():
            raise CapabilityUnavailableError(f"Capability unavailable: {capability}")
        return adapter
