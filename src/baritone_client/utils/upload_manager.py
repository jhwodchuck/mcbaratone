"""UploadManager facade for schematic uploads with progress tracking."""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from ..transport.transport import Transport

logger = logging.getLogger(__name__)


class UploadStatus(Enum):
    """Upload status enumeration."""
    INITIALIZING = "initializing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class UploadPriority(Enum):
    """Upload priority levels."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class UploadProgress:
    """Upload progress information."""

    def __init__(self, data: Dict[str, Any]):
        self.name = data.get("name", "")
        self.status = UploadStatus(data.get("status", "initializing"))
        self.progress_percentage = data.get("progress_percentage", 0.0)
        self.received_bytes = data.get("received_bytes", 0)
        self.expected_size = data.get("expected_size", 0)
        self.start_time = data.get("start_time", 0)
        self.last_activity = data.get("last_activity", 0)
        self.priority = UploadPriority(data.get("priority", "normal"))
        self.error_message = data.get("error_message")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "status": self.status.value,
            "progress_percentage": self.progress_percentage,
            "received_bytes": self.received_bytes,
            "expected_size": self.expected_size,
            "start_time": self.start_time,
            "last_activity": self.last_activity,
            "priority": self.priority.value,
            "error_message": self.error_message
        }


class UploadManager:
    """Facade for bridge-side upload management."""

    def __init__(self, transport: Transport):
        self.transport = transport

    def start_upload(
        self,
        name: str,
        expected_size: int,
        priority: UploadPriority = UploadPriority.NORMAL
    ) -> bool:
        """
        Start a new upload.

        Args:
            name: Upload name/identifier
            expected_size: Expected file size in bytes
            priority: Upload priority

        Returns:
            True if started successfully
        """
        response = self.transport.dispatch("upload/start", {
            "name": name,
            "expected_size": expected_size,
            "priority": priority.value
        })
        return response.get("success", False)

    def upload_chunk(self, name: str, data: bytes) -> bool:
        """
        Upload a chunk of data.

        Args:
            name: Upload name
            data: Chunk data

        Returns:
            True if processed successfully
        """
        # For binary data, we might need to encode as base64 or similar
        # For now, assume the transport handles it
        response = self.transport.dispatch("upload/chunk", {
            "name": name,
            "data": data  # Transport should handle serialization
        })
        return response.get("success", False)

    def complete_upload(self, name: str, expected_sha256: Optional[str] = None) -> bool:
        """
        Complete an upload.

        Args:
            name: Upload name
            expected_sha256: Expected SHA256 hash for validation

        Returns:
            True if completed successfully
        """
        payload = {"name": name}
        if expected_sha256:
            payload["expected_sha256"] = expected_sha256
        response = self.transport.dispatch("upload/complete", payload)
        return response.get("success", False)

    def cancel_upload(self, name: str) -> bool:
        """
        Cancel an upload.

        Args:
            name: Upload name

        Returns:
            True if cancelled
        """
        response = self.transport.dispatch("upload/cancel", {"name": name})
        return response.get("success", False)

    def get_upload_progress(self, name: str) -> Optional[UploadProgress]:
        """
        Get upload progress information.

        Args:
            name: Upload name

        Returns:
            UploadProgress object or None if not found
        """
        response = self.transport.dispatch("upload/progress", {"name": name})
        if "error" in response:
            return None
        return UploadProgress(response)

    def get_active_uploads(self) -> List[UploadProgress]:
        """
        Get all active uploads.

        Returns:
            List of UploadProgress objects
        """
        response = self.transport.dispatch("upload/active", {})
        uploads_data = response.get("uploads", [])
        return [UploadProgress(data) for data in uploads_data]

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get upload statistics.

        Returns:
            Statistics dictionary
        """
        response = self.transport.dispatch("upload/stats", {})
        return response.get("stats", {})