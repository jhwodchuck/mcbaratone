"""Stable world identity used to protect persistent checkpoints."""

from __future__ import annotations

import hashlib
import gzip
import json
import struct
from dataclasses import dataclass
from pathlib import Path
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


class _NbtReader:
    """Small, dependency-free reader for the scalar data in ``level.dat``."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def take(self, size: int) -> bytes:
        end = self.offset + size
        if size < 0 or end > len(self.payload):
            raise ValueError("truncated NBT payload")
        value = self.payload[self.offset:end]
        self.offset = end
        return value

    def unpack(self, pattern: str):
        size = struct.calcsize(pattern)
        return struct.unpack(pattern, self.take(size))[0]

    def string(self) -> str:
        length = self.unpack(">H")
        return self.take(length).decode("utf-8")

    def value(self, tag: int):
        if tag == 1:
            return self.unpack(">b")
        if tag == 2:
            return self.unpack(">h")
        if tag == 3:
            return self.unpack(">i")
        if tag == 4:
            return self.unpack(">q")
        if tag == 5:
            return self.unpack(">f")
        if tag == 6:
            return self.unpack(">d")
        if tag == 7:
            return self.take(self.unpack(">i"))
        if tag == 8:
            return self.string()
        if tag == 9:
            element_tag = self.unpack(">B")
            length = self.unpack(">i")
            if length < 0:
                raise ValueError("negative NBT list length")
            return [self.value(element_tag) for _ in range(length)]
        if tag == 10:
            compound = {}
            while True:
                child_tag = self.unpack(">B")
                if child_tag == 0:
                    return compound
                child_name = self.string()
                compound[child_name] = self.value(child_tag)
        if tag == 11:
            length = self.unpack(">i")
            if length < 0:
                raise ValueError("negative NBT int-array length")
            return [self.unpack(">i") for _ in range(length)]
        if tag == 12:
            length = self.unpack(">i")
            if length < 0:
                raise ValueError("negative NBT long-array length")
            return [self.unpack(">q") for _ in range(length)]
        raise ValueError(f"unsupported NBT tag {tag}")


def read_level_dat_seed(path: str | Path) -> Optional[int]:
    """Read the authoritative Java-world seed from a gzipped ``level.dat``."""
    try:
        with gzip.open(Path(path), "rb") as handle:
            reader = _NbtReader(handle.read())
        if reader.unpack(">B") != 10:
            return None
        reader.string()  # Root compound name, normally empty.
        root = reader.value(10)
        data = root.get("Data", {}) if isinstance(root, Mapping) else {}
        settings = data.get("WorldGenSettings", {}) if isinstance(data, Mapping) else {}
        seed = settings.get("seed") if isinstance(settings, Mapping) else None
        return int(seed) if seed is not None else None
    except (OSError, EOFError, UnicodeDecodeError, ValueError, struct.error):
        return None
