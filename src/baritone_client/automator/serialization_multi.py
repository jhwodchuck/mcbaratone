"""
Multi-format Serialization for State Management.

Provides support for JSON, MessagePack, and compressed serialization formats.
"""

import json
import gzip
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any, Optional, BinaryIO, TextIO, Union
from pathlib import Path

logger = logging.getLogger(__name__)


class SerializationFormat(Enum):
    """Supported serialization formats."""
    JSON = "json"
    MSGPACK = "msgpack"
    JSON_GZ = "json.gz"
    MSGPACK_GZ = "msgpack.gz"


class SerializationError(Exception):
    """Exception raised when serialization/deserialization fails."""
    pass


class Serializer(ABC):
    """Abstract base class for data serializers."""

    @abstractmethod
    def serialize(self, data: Dict[str, Any]) -> bytes:
        """
        Serialize data to bytes.

        Args:
            data: Data to serialize

        Returns:
            Serialized data as bytes
        """
        pass

    @abstractmethod
    def deserialize(self, data: bytes) -> Dict[str, Any]:
        """
        Deserialize bytes to data.

        Args:
            data: Serialized data as bytes

        Returns:
            Deserialized data
        """
        pass

    @abstractmethod
    def get_format(self) -> SerializationFormat:
        """Get the serialization format."""
        pass


class JSONSerializer(Serializer):
    """JSON-based serializer."""

    def serialize(self, data: Dict[str, Any]) -> bytes:
        """Serialize data to JSON bytes."""
        try:
            json_str = json.dumps(data, indent=2, sort_keys=True)
            return json_str.encode('utf-8')
        except (TypeError, ValueError) as e:
            raise SerializationError(f"JSON serialization failed: {str(e)}") from e

    def deserialize(self, data: bytes) -> Dict[str, Any]:
        """Deserialize JSON bytes to data."""
        try:
            json_str = data.decode('utf-8')
            return json.loads(json_str)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise SerializationError(f"JSON deserialization failed: {str(e)}") from e

    def get_format(self) -> SerializationFormat:
        """Get the serialization format."""
        return SerializationFormat.JSON


class MsgPackSerializer(Serializer):
    """MessagePack-based serializer."""

    def __init__(self):
        """Initialize MsgPack serializer."""
        self._msgpack = None
        try:
            import msgpack
            self._msgpack = msgpack
        except ImportError:
            logger.warning("msgpack not available, MsgPackSerializer will not function")

    def serialize(self, data: Dict[str, Any]) -> bytes:
        """Serialize data to MessagePack bytes."""
        if self._msgpack is None:
            raise SerializationError("msgpack library not available")

        try:
            return self._msgpack.packb(data, use_bin_type=True)
        except Exception as e:
            raise SerializationError(f"MessagePack serialization failed: {str(e)}") from e

    def deserialize(self, data: bytes) -> Dict[str, Any]:
        """Deserialize MessagePack bytes to data."""
        if self._msgpack is None:
            raise SerializationError("msgpack library not available")

        try:
            return self._msgpack.unpackb(data, raw=False)
        except Exception as e:
            raise SerializationError(f"MessagePack deserialization failed: {str(e)}") from e

    def get_format(self) -> SerializationFormat:
        """Get the serialization format."""
        return SerializationFormat.MSGPACK


class CompressedSerializer:
    """Wrapper for compressed serialization."""

    def __init__(self, base_serializer: Serializer, compression_level: int = 6):
        """
        Initialize compressed serializer.

        Args:
            base_serializer: Base serializer to wrap
            compression_level: Gzip compression level (1-9)
        """
        self._base_serializer = base_serializer
        self._compression_level = compression_level

    def serialize(self, data: Dict[str, Any]) -> bytes:
        """Serialize and compress data."""
        uncompressed = self._base_serializer.serialize(data)
        try:
            return gzip.compress(uncompressed, compresslevel=self._compression_level)
        except Exception as e:
            raise SerializationError(f"Compression failed: {str(e)}") from e

    def deserialize(self, data: bytes) -> Dict[str, Any]:
        """Decompress and deserialize data."""
        try:
            uncompressed = gzip.decompress(data)
            return self._base_serializer.deserialize(uncompressed)
        except Exception as e:
            raise SerializationError(f"Decompression failed: {str(e)}") from e

    def get_format(self) -> SerializationFormat:
        """Get the serialization format."""
        base_format = self._base_serializer.get_format()
        if base_format == SerializationFormat.JSON:
            return SerializationFormat.JSON_GZ
        elif base_format == SerializationFormat.MSGPACK:
            return SerializationFormat.MSGPACK_GZ
        else:
            raise SerializationError(f"Unsupported base format for compression: {base_format}")


class MultiFormatSerializer:
    """Multi-format serializer supporting multiple serialization formats."""

    def __init__(self):
        """Initialize multi-format serializer."""
        self._serializers = {
            SerializationFormat.JSON: JSONSerializer(),
            SerializationFormat.MSGPACK: MsgPackSerializer(),
        }

        # Initialize compressed serializers
        for fmt in [SerializationFormat.JSON, SerializationFormat.MSGPACK]:
            if fmt in self._serializers:
                compressed_fmt = SerializationFormat(f"{fmt.value}.gz")
                self._serializers[compressed_fmt] = CompressedSerializer(self._serializers[fmt])

    def serialize(self, data: Dict[str, Any], format: SerializationFormat = SerializationFormat.JSON) -> bytes:
        """
        Serialize data using specified format.

        Args:
            data: Data to serialize
            format: Serialization format to use

        Returns:
            Serialized data as bytes
        """
        if format not in self._serializers:
            raise SerializationError(f"Unsupported serialization format: {format}")

        return self._serializers[format].serialize(data)

    def deserialize(self, data: bytes, format: Optional[SerializationFormat] = None) -> Dict[str, Any]:
        """
        Deserialize data, auto-detecting format if not specified.

        Args:
            data: Serialized data as bytes
            format: Serialization format (auto-detected if None)

        Returns:
            Deserialized data
        """
        if format is None:
            format = self._detect_format(data)

        if format not in self._serializers:
            raise SerializationError(f"Unsupported deserialization format: {format}")

        return self._serializers[format].deserialize(data)

    def _detect_format(self, data: bytes) -> SerializationFormat:
        """
        Auto-detect serialization format from data.

        Args:
            data: Serialized data

        Returns:
            Detected format
        """
        # Check for gzip compression first
        if len(data) >= 2 and data[0:2] == b'\x1f\x8b':
            # Decompress first few bytes to check content
            try:
                decompressed_start = gzip.decompress(data[:100])  # Just first 100 bytes
                if decompressed_start.startswith(b'{'):
                    return SerializationFormat.JSON_GZ
                else:
                    return SerializationFormat.MSGPACK_GZ
            except Exception:
                pass

        # Check uncompressed formats
        if len(data) > 0:
            if data[0:1] == b'{':
                return SerializationFormat.JSON
            else:
                # Assume MessagePack for binary data
                return SerializationFormat.MSGPACK

        raise SerializationError("Cannot auto-detect serialization format")

    def get_supported_formats(self) -> list[SerializationFormat]:
        """Get list of supported serialization formats."""
        return list(self._serializers.keys())

    def is_format_supported(self, format: SerializationFormat) -> bool:
        """Check if a format is supported."""
        return format in self._serializers


class FileSerializer:
    """File-based serialization with format detection and metadata."""

    def __init__(self, serializer: Optional[MultiFormatSerializer] = None):
        """
        Initialize file serializer.

        Args:
            serializer: Multi-format serializer to use
        """
        self._serializer = serializer or MultiFormatSerializer()

    def save_to_file(self, data: Dict[str, Any], filepath: Union[str, Path],
                    format: Optional[SerializationFormat] = None,
                    add_metadata: bool = True) -> None:
        """
        Save data to file with optional metadata.

        Args:
            data: Data to save
            filepath: File path to save to
            format: Serialization format (auto-selected if None)
            add_metadata: Whether to add version/format metadata
        """
        filepath = Path(filepath)

        # Auto-select format based on extension if not specified
        if format is None:
            format = self._detect_format_from_extension(filepath)

        # Add metadata if requested
        if add_metadata:
            data_with_meta = self._add_metadata(data, format)
        else:
            data_with_meta = data

        # Serialize data
        serialized = self._serializer.serialize(data_with_meta, format)

        # Write to file
        try:
            with open(filepath, 'wb') as f:
                f.write(serialized)
        except Exception as e:
            raise SerializationError(f"Failed to write to file {filepath}: {str(e)}") from e

    def load_from_file(self, filepath: Union[str, Path]) -> Dict[str, Any]:
        """
        Load data from file with automatic format detection.

        Args:
            filepath: File path to load from

        Returns:
            Deserialized data (metadata stripped)
        """
        filepath = Path(filepath)

        if not filepath.exists():
            raise SerializationError(f"File does not exist: {filepath}")

        try:
            with open(filepath, 'rb') as f:
                data_bytes = f.read()
        except Exception as e:
            raise SerializationError(f"Failed to read file {filepath}: {str(e)}") from e

        # Deserialize data
        data = self._serializer.deserialize(data_bytes)

        # Strip metadata if present
        return self._strip_metadata(data)

    def _detect_format_from_extension(self, filepath: Path) -> SerializationFormat:
        """Detect format from file extension."""
        suffix = filepath.suffix.lower()

        if suffix == '.json':
            return SerializationFormat.JSON
        elif suffix == '.msgpack':
            return SerializationFormat.MSGPACK
        elif suffix == '.gz':
            # Check double extension
            stem_suffix = Path(filepath.stem).suffix.lower()
            if stem_suffix == '.json':
                return SerializationFormat.JSON_GZ
            elif stem_suffix == '.msgpack':
                return SerializationFormat.MSGPACK_GZ

        # Default to JSON
        return SerializationFormat.JSON

    def _add_metadata(self, data: Dict[str, Any], format: SerializationFormat) -> Dict[str, Any]:
        """Add serialization metadata to data."""
        from .versioning import VersionManager  # Import here to avoid circular imports

        version_manager = VersionManager()
        current_version = version_manager.get_current_version()

        metadata = {
            '_metadata': {
                'schema_version': str(current_version),
                'serialization_format': format.value,
                'created_at': __import__('time').time(),
                'created_by': 'baritone_client'
            }
        }

        # Merge metadata with data (metadata takes precedence)
        return {**data, **metadata}

    def _strip_metadata(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Strip serialization metadata from data."""
        if '_metadata' in data:
            data_copy = data.copy()
            del data_copy['_metadata']
            return data_copy
        return data