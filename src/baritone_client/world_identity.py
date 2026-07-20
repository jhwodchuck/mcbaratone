"""Stable world identity used to protect persistent checkpoints."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class WorldIdentity:
    """World-scoped identity, deliberately independent of a connection session."""

    seed: Optional[int] = None
    world_name: Optional[str] = None
    server_address: Optional[str] = None
    version: int = 1

    @classmethod
    def from_state(
        cls,
        state: Mapping[str, Any],
        *,
        server_address: Optional[str] = None,
    ) -> "WorldIdentity":
        embedded = state.get("world_identity")
        source = embedded if isinstance(embedded, Mapping) else state
        seed = source.get("seed", state.get("world_seed"))
        return cls(
            seed=int(seed) if seed is not None else None,
            world_name=_clean(source.get("world_name")),
            server_address=_clean(source.get("server_address") or server_address),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorldIdentity":
        return cls.from_state(value)

    @property
    def available(self) -> bool:
        return self.seed is not None or bool(self.world_name or self.server_address)

    @property
    def strength(self) -> str:
        if self.seed is not None and (self.world_name or self.server_address):
            return "strong"
        if self.seed is not None:
            return "seed"
        if self.world_name or self.server_address:
            return "scope_only"
        return "unavailable"

    def canonical_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {"version": self.version}
        if self.seed is not None:
            fields["seed"] = self.seed
        if self.world_name:
            fields["world_name"] = self.world_name
        if self.server_address:
            fields["server_address"] = self.server_address.lower()
        return fields

    @property
    def stable_hash(self) -> Optional[str]:
        if not self.available:
            return None
        canonical = json.dumps(
            self.canonical_fields(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "mcbaratone-world-v1:" + hashlib.sha256(canonical).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self.canonical_fields()
        result["strength"] = self.strength
        result["stable_hash"] = self.stable_hash
        return result

    def matches(self, other: "WorldIdentity") -> bool:
        if not self.available or not other.available:
            return False
        if self.seed is not None and other.seed is not None and self.seed != other.seed:
            return False
        if self.world_name and other.world_name and self.world_name != other.world_name:
            return False
        if (
            self.server_address
            and other.server_address
            and self.server_address.lower() != other.server_address.lower()
        ):
            return False
        shared_authoritative_field = (
            self.seed is not None and other.seed is not None
        ) or bool(self.world_name and other.world_name) or bool(
            self.server_address and other.server_address
        )
        return shared_authoritative_field


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
