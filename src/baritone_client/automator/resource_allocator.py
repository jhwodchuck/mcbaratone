"""
Advanced Resource Allocator - Phase 3 Implementation

REFACTORED: Implementation moved to `src/baritone_client/automator/allocation/`
"""

from .allocation import (
    ResourceAllocator,
    ConflictDetector,
    ResourceForecaster,
    ConflictResolver
)

__all__ = [
    "ResourceAllocator",
    "ConflictDetector",
    "ResourceForecaster",
    "ConflictResolver"
]