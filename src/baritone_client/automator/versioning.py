"""
Versioning System for State Management.

Provides semantic versioning support for checkpoint files and state migration.
"""

import re
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class SemanticVersion:
    """Semantic version following MAJOR.MINOR.PATCH format."""

    major: int
    minor: int
    patch: int
    prerelease: Optional[str] = None
    build: Optional[str] = None

    def __str__(self) -> str:
        """String representation of semantic version."""
        version = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            version += f"-{self.prerelease}"
        if self.build:
            version += f"+{self.build}"
        return version

    def __lt__(self, other: 'SemanticVersion') -> bool:
        """Compare versions for ordering."""
        return self._compare(other) < 0

    def __le__(self, other: 'SemanticVersion') -> bool:
        """Compare versions for ordering."""
        return self._compare(other) <= 0

    def __gt__(self, other: 'SemanticVersion') -> bool:
        """Compare versions for ordering."""
        return self._compare(other) > 0

    def __ge__(self, other: 'SemanticVersion') -> bool:
        """Compare versions for ordering."""
        return self._compare(other) >= 0

    def __eq__(self, other: 'SemanticVersion') -> bool:
        """Compare versions for equality."""
        return self._compare(other) == 0

    def __ne__(self, other: 'SemanticVersion') -> bool:
        """Compare versions for inequality."""
        return self._compare(other) != 0

    def _compare(self, other: 'SemanticVersion') -> int:
        """Internal comparison method."""
        # Compare major, minor, patch
        for self_val, other_val in [(self.major, other.major),
                                   (self.minor, other.minor),
                                   (self.patch, other.patch)]:
            if self_val != other_val:
                return (self_val > other_val) - (self_val < other_val)

        # Handle prerelease (prerelease versions are lower than release versions)
        if self.prerelease and not other.prerelease:
            return -1
        elif not self.prerelease and other.prerelease:
            return 1
        elif self.prerelease and other.prerelease:
            return (self.prerelease > other.prerelease) - (self.prerelease < other.prerelease)

        return 0

    @classmethod
    def parse(cls, version_str: str) -> 'SemanticVersion':
        """Parse semantic version from string."""
        # Regex for semantic versioning
        pattern = r'^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9\.\-]+))?(?:\+([a-zA-Z0-9\.\-]+))?$'
        match = re.match(pattern, version_str)

        if not match:
            raise ValueError(f"Invalid semantic version: {version_str}")

        major, minor, patch, prerelease, build = match.groups()

        return cls(
            major=int(major),
            minor=int(minor),
            patch=int(patch),
            prerelease=prerelease,
            build=build
        )

    def is_compatible(self, other: 'SemanticVersion') -> bool:
        """Check if versions are compatible (same major version)."""
        return self.major == other.major

    def is_breaking_change(self, other: 'SemanticVersion') -> bool:
        """Check if this version represents a breaking change from other."""
        return self.major > other.major


class VersionManager:
    """Manages versioning for state checkpoints and migrations."""

    CURRENT_VERSION = SemanticVersion(1, 0, 0)

    def __init__(self):
        """Initialize version manager."""
        self._current_version = self.CURRENT_VERSION

    def get_current_version(self) -> SemanticVersion:
        """Get current schema version."""
        return self._current_version

    def set_current_version(self, version: SemanticVersion) -> None:
        """Set current schema version."""
        self._current_version = version

    def parse_version(self, version_str: str) -> SemanticVersion:
        """Parse version string into SemanticVersion."""
        return SemanticVersion.parse(version_str)

    def validate_version(self, version: SemanticVersion) -> bool:
        """Validate version format and compatibility."""
        try:
            # Basic validation - version must be parseable and non-negative
            return (version.major >= 0 and version.minor >= 0 and version.patch >= 0 and
                   (version.prerelease is None or isinstance(version.prerelease, str)) and
                   (version.build is None or isinstance(version.build, str)))
        except Exception:
            return False

    def get_version_chain(self, from_version: SemanticVersion, to_version: SemanticVersion) -> List[SemanticVersion]:
        """Get version chain for migration (simplified - assumes linear progression)."""
        if from_version >= to_version:
            return []

        # For now, return incremental versions
        # In a real implementation, this would reference a migration graph
        versions = []
        current = SemanticVersion(from_version.major, from_version.minor, from_version.patch)

        while current < to_version:
            if current.patch < 999:  # Arbitrary limit
                current = SemanticVersion(current.major, current.minor, current.patch + 1)
            elif current.minor < 999:
                current = SemanticVersion(current.major, current.minor + 1, 0)
            else:
                current = SemanticVersion(current.major + 1, 0, 0)

            if current <= to_version:
                versions.append(current)

        return versions