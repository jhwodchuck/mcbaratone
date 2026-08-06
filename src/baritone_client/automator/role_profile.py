"""Durable, forward-compatible fleet role profiles.

Unlike :class:`FleetRole`, profiles intentionally keep role names as strings.
That lets a controller advertise a new specialty before every scheduler knows
about it, while retaining the legacy ``fleet-role.txt`` checkpoint contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Optional, Union


DEFAULT_PRIMARY_ROLE = "balanced"
PROFILE_FILE_NAME = "fleet-role-profile.json"
LEGACY_FILE_NAME = "fleet-role.txt"


def normalize_role_name(value: Any) -> Optional[str]:
    """Return a stable role identifier, or ``None`` for unusable input."""
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[\s-]+", "_", value.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or None


def _role_list(value: Any, primary: str, field_name: str, errors: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        errors.append(f"{field_name} must be a list of role names")
        return ()
    roles: list[str] = []
    for candidate in value:
        role = normalize_role_name(candidate)
        if role is None:
            errors.append(f"{field_name} contains an invalid role")
        elif role != primary and role not in roles:
            roles.append(role)
    return tuple(roles)


def _mapping(value: Any, field_name: str, errors: list[str]) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        errors.append(f"{field_name} must be an object")
        return {}
    return dict(value)


@dataclass(frozen=True)
class RoleProfile:
    """One controller's durable scope, capacity, and emergency coverage."""

    primary_role: str = DEFAULT_PRIMARY_ROLE
    secondary_roles: tuple[str, ...] = ()
    on_call_roles: tuple[str, ...] = ()
    home_anchor: Optional[dict[str, Any]] = None
    service_radius: Optional[float] = None
    permitted_dimensions: tuple[str, ...] = ()
    risk_policy: dict[str, Any] = field(default_factory=dict)
    reserved_inventory: dict[str, Any] = field(default_factory=dict)
    production_targets: dict[str, Any] = field(default_factory=dict)
    cooldowns: dict[str, Any] = field(default_factory=dict)
    current_assignment: Any = None
    emergency_responsibilities: tuple[str, ...] = ()
    validation_errors: tuple[str, ...] = field(default_factory=tuple, compare=False)
    source: str = field(default="default", compare=False)

    def __post_init__(self) -> None:
        primary = normalize_role_name(self.primary_role) or DEFAULT_PRIMARY_ROLE
        errors = list(self.validation_errors)
        if primary != self.primary_role:
            errors.append("primary_role was normalized or replaced")
        secondary = _role_list(self.secondary_roles, primary, "secondary_roles", errors)
        on_call = _role_list(self.on_call_roles, primary, "on_call_roles", errors)
        emergency = _role_list(
            self.emergency_responsibilities, primary, "emergency_responsibilities", errors
        )
        dimensions = _role_list(self.permitted_dimensions, "", "permitted_dimensions", errors)
        radius = self.service_radius
        if radius is not None:
            try:
                radius = float(radius)
                if radius < 0:
                    raise ValueError
            except (TypeError, ValueError):
                radius = None
                errors.append("service_radius must be a non-negative number")
        anchor = self.home_anchor
        if anchor is not None and not isinstance(anchor, Mapping):
            anchor = None
            errors.append("home_anchor must be an object")
        object.__setattr__(self, "primary_role", primary)
        object.__setattr__(self, "secondary_roles", secondary)
        object.__setattr__(self, "on_call_roles", on_call)
        object.__setattr__(self, "emergency_responsibilities", emergency)
        object.__setattr__(self, "permitted_dimensions", dimensions)
        object.__setattr__(self, "service_radius", radius)
        object.__setattr__(self, "home_anchor", dict(anchor) if anchor else None)
        object.__setattr__(self, "risk_policy", _mapping(self.risk_policy, "risk_policy", errors))
        object.__setattr__(self, "reserved_inventory", _mapping(self.reserved_inventory, "reserved_inventory", errors))
        object.__setattr__(self, "production_targets", _mapping(self.production_targets, "production_targets", errors))
        object.__setattr__(self, "cooldowns", _mapping(self.cooldowns, "cooldowns", errors))
        object.__setattr__(self, "validation_errors", tuple(dict.fromkeys(errors)))

    @property
    def valid(self) -> bool:
        """Whether loading required no recovery or field-level repair."""
        return not self.validation_errors

    def eligible_roles(self) -> tuple[str, ...]:
        """Return primary, then secondary and on-call roles without duplicates."""
        return tuple(dict.fromkeys((self.primary_role, *self.secondary_roles, *self.on_call_roles)))

    def supports(self, role: Any) -> bool:
        """Report whether this profile can accept a normalized role name."""
        normalized = normalize_role_name(role)
        return normalized is not None and normalized in self.eligible_roles()

    def to_dict(self) -> dict[str, Any]:
        """Serialize durable profile data, excluding local load diagnostics."""
        return {
            "primary_role": self.primary_role,
            "secondary_roles": list(self.secondary_roles),
            "on_call_roles": list(self.on_call_roles),
            "home_anchor": self.home_anchor,
            "service_radius": self.service_radius,
            "permitted_dimensions": list(self.permitted_dimensions),
            "risk_policy": self.risk_policy,
            "reserved_inventory": self.reserved_inventory,
            "production_targets": self.production_targets,
            "cooldowns": self.cooldowns,
            "current_assignment": self.current_assignment,
            "emergency_responsibilities": list(self.emergency_responsibilities),
        }

    @classmethod
    def from_dict(cls, data: Any, *, fallback_primary: Any = None, source: str = "json") -> "RoleProfile":
        """Build a profile while recording invalid fields instead of raising."""
        fallback = normalize_role_name(fallback_primary) or DEFAULT_PRIMARY_ROLE
        if not isinstance(data, Mapping):
            return cls(primary_role=fallback, validation_errors=("profile must be an object",), source=source)
        raw_primary = data.get("primary_role", fallback)
        primary = normalize_role_name(raw_primary)
        errors: list[str] = []
        if primary is None:
            primary = fallback
            errors.append("primary_role must be a non-empty string")
        return cls(
            primary_role=primary,
            secondary_roles=data.get("secondary_roles", ()),
            on_call_roles=data.get("on_call_roles", ()),
            home_anchor=data.get("home_anchor"),
            service_radius=data.get("service_radius"),
            permitted_dimensions=data.get("permitted_dimensions", ()),
            risk_policy=data.get("risk_policy"),
            reserved_inventory=data.get("reserved_inventory"),
            production_targets=data.get("production_targets"),
            cooldowns=data.get("cooldowns"),
            current_assignment=data.get("current_assignment"),
            emergency_responsibilities=data.get("emergency_responsibilities", ()),
            validation_errors=tuple(errors),
            source=source,
        )


def _fallback_primary(primary_role: Any, env: Optional[Mapping[str, str]]) -> str:
    environment = os.environ if env is None else env
    return normalize_role_name(primary_role) or normalize_role_name(environment.get("MC_FLEET_ROLE")) or DEFAULT_PRIMARY_ROLE


def load_role_profile(
    checkpoint_dir: Union[str, Path], primary_role: Any = None, env: Optional[Mapping[str, str]] = None
) -> RoleProfile:
    """Load JSON first, then legacy text, then a supplied/environment default.

    A bad checkpoint is never fatal.  ``validation_errors`` explains why the
    fallback happened, allowing operators to repair it without losing control.
    """
    directory = Path(checkpoint_dir)
    fallback = _fallback_primary(primary_role, env)
    json_path = directory / PROFILE_FILE_NAME
    try:
        if json_path.exists():
            try:
                profile = RoleProfile.from_dict(json.loads(json_path.read_text(encoding="utf-8")), fallback_primary=fallback)
                if profile.valid:
                    return profile
                json_error = (
                    f"invalid {PROFILE_FILE_NAME}: "
                    + "; ".join(str(error) for error in profile.validation_errors)
                )
            except (OSError, json.JSONDecodeError) as error:
                json_error = f"could not load {PROFILE_FILE_NAME}: {error}"
        else:
            json_error = ""
        legacy_path = directory / LEGACY_FILE_NAME
        if legacy_path.exists():
            legacy = normalize_role_name(legacy_path.read_text(encoding="utf-8"))
            if legacy:
                return RoleProfile(
                    primary_role=legacy,
                    source="legacy",
                    validation_errors=(json_error,) if json_error else (),
                )
            json_error = json_error or f"{LEGACY_FILE_NAME} has no valid primary role"
    except OSError as error:
        json_error = f"could not read role profile: {error}"
    return RoleProfile(
        primary_role=fallback,
        source="fallback",
        validation_errors=(json_error,) if json_error else (),
    )


def save_role_profile(checkpoint_dir: Union[str, Path], profile: RoleProfile) -> Path:
    """Atomically persist a profile JSON checkpoint and return its path."""
    directory = Path(checkpoint_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / PROFILE_FILE_NAME
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as handle:
        json.dump(profile.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    try:
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


__all__ = ["DEFAULT_PRIMARY_ROLE", "RoleProfile", "load_role_profile", "normalize_role_name", "save_role_profile"]
