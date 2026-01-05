"""
Distributed State Storage Abstraction.

Provides abstraction layer for different storage backends supporting high availability.
"""

import hashlib
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any, Optional, List, Union
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Exception raised when storage operations fail."""
    pass


class StorageBackend(Enum):
    """Supported storage backends."""
    FILESYSTEM = "filesystem"
    MEMORY = "memory"
    # Future: S3, GCS, Redis, etc.


@dataclass
class StorageMetadata:
    """Metadata for stored data."""
    key: str
    size: int
    checksum: str
    created_at: float
    modified_at: float
    version: Optional[str] = None
    tags: Optional[Dict[str, str]] = None


class StorageProvider(ABC):
    """Abstract base class for storage providers."""

    @abstractmethod
    async def store(self, key: str, data: bytes, metadata: Optional[StorageMetadata] = None) -> None:
        """
        Store data with given key.

        Args:
            key: Unique identifier for the data
            data: Data to store as bytes
            metadata: Optional metadata
        """
        pass

    @abstractmethod
    async def retrieve(self, key: str) -> bytes:
        """
        Retrieve data by key.

        Args:
            key: Unique identifier for the data

        Returns:
            Stored data as bytes
        """
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """
        Check if key exists.

        Args:
            key: Key to check

        Returns:
            True if key exists
        """
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        """
        Delete data by key.

        Args:
            key: Key to delete
        """
        pass

    @abstractmethod
    async def list_keys(self, prefix: Optional[str] = None) -> List[str]:
        """
        List all keys with optional prefix.

        Args:
            prefix: Optional prefix to filter keys

        Returns:
            List of keys
        """
        pass

    @abstractmethod
    async def get_metadata(self, key: str) -> Optional[StorageMetadata]:
        """
        Get metadata for key.

        Args:
            key: Key to get metadata for

        Returns:
            Metadata if key exists, None otherwise
        """
        pass


class FilesystemStorageProvider(StorageProvider):
    """Filesystem-based storage provider."""

    def __init__(self, base_path: Union[str, Path], create_dirs: bool = True):
        """
        Initialize filesystem storage provider.

        Args:
            base_path: Base directory for storage
            create_dirs: Whether to create directories if they don't exist
        """
        self._base_path = Path(base_path)
        if create_dirs and not self._base_path.exists():
            self._base_path.mkdir(parents=True, exist_ok=True)

    async def store(self, key: str, data: bytes, metadata: Optional[StorageMetadata] = None) -> None:
        """Store data to filesystem."""
        file_path = self._base_path / key

        # Create parent directories if needed
        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Write data
            with open(file_path, 'wb') as f:
                f.write(data)

            # Write metadata if provided
            if metadata:
                metadata_path = file_path.with_suffix('.meta')
                import json
                with open(metadata_path, 'w') as f:
                    json.dump({
                        'key': metadata.key,
                        'size': metadata.size,
                        'checksum': metadata.checksum,
                        'created_at': metadata.created_at,
                        'modified_at': metadata.modified_at,
                        'version': metadata.version,
                        'tags': metadata.tags or {}
                    }, f, indent=2)

        except Exception as e:
            raise StorageError(f"Failed to store data for key '{key}': {str(e)}") from e

    async def retrieve(self, key: str) -> bytes:
        """Retrieve data from filesystem."""
        file_path = self._base_path / key

        if not file_path.exists():
            raise StorageError(f"Key '{key}' does not exist")

        try:
            with open(file_path, 'rb') as f:
                return f.read()
        except Exception as e:
            raise StorageError(f"Failed to retrieve data for key '{key}': {str(e)}") from e

    async def exists(self, key: str) -> bool:
        """Check if key exists in filesystem."""
        file_path = self._base_path / key
        return file_path.exists()

    async def delete(self, key: str) -> None:
        """Delete data from filesystem."""
        file_path = self._base_path / key
        metadata_path = file_path.with_suffix('.meta')

        try:
            if file_path.exists():
                file_path.unlink()
            if metadata_path.exists():
                metadata_path.unlink()
        except Exception as e:
            raise StorageError(f"Failed to delete data for key '{key}': {str(e)}") from e

    async def list_keys(self, prefix: Optional[str] = None) -> List[str]:
        """List keys in filesystem."""
        try:
            all_files = []
            for file_path in self._base_path.rglob('*'):
                if file_path.is_file() and not file_path.name.endswith('.meta'):
                    key = str(file_path.relative_to(self._base_path))
                    if prefix is None or key.startswith(prefix):
                        all_files.append(key)
            return sorted(all_files)
        except Exception as e:
            raise StorageError(f"Failed to list keys: {str(e)}") from e

    async def get_metadata(self, key: str) -> Optional[StorageMetadata]:
        """Get metadata for key."""
        metadata_path = (self._base_path / key).with_suffix('.meta')

        if not metadata_path.exists():
            # Generate basic metadata if file exists
            file_path = self._base_path / key
            if file_path.exists():
                stat = file_path.stat()
                import time
                return StorageMetadata(
                    key=key,
                    size=stat.st_size,
                    checksum=self._calculate_checksum(file_path),
                    created_at=stat.st_ctime,
                    modified_at=stat.st_mtime
                )
            return None

        try:
            import json
            with open(metadata_path, 'r') as f:
                data = json.load(f)
            return StorageMetadata(
                key=data['key'],
                size=data['size'],
                checksum=data['checksum'],
                created_at=data['created_at'],
                modified_at=data['modified_at'],
                version=data.get('version'),
                tags=data.get('tags', {})
            )
        except Exception as e:
            logger.warning(f"Failed to read metadata for key '{key}': {str(e)}")
            return None

    def _calculate_checksum(self, file_path: Path) -> str:
        """Calculate SHA256 checksum of file."""
        hash_sha256 = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_sha256.update(chunk)
            return hash_sha256.hexdigest()
        except Exception:
            return ""


class MemoryStorageProvider(StorageProvider):
    """In-memory storage provider for testing and caching."""

    def __init__(self):
        """Initialize memory storage provider."""
        self._storage: Dict[str, bytes] = {}
        self._metadata: Dict[str, StorageMetadata] = {}

    async def store(self, key: str, data: bytes, metadata: Optional[StorageMetadata] = None) -> None:
        """Store data in memory."""
        import time
        self._storage[key] = data

        if metadata:
            self._metadata[key] = metadata
        else:
            # Generate basic metadata
            self._metadata[key] = StorageMetadata(
                key=key,
                size=len(data),
                checksum=hashlib.sha256(data).hexdigest(),
                created_at=time.time(),
                modified_at=time.time()
            )

    async def retrieve(self, key: str) -> bytes:
        """Retrieve data from memory."""
        if key not in self._storage:
            raise StorageError(f"Key '{key}' does not exist")
        return self._storage[key]

    async def exists(self, key: str) -> bool:
        """Check if key exists in memory."""
        return key in self._storage

    async def delete(self, key: str) -> None:
        """Delete data from memory."""
        self._storage.pop(key, None)
        self._metadata.pop(key, None)

    async def list_keys(self, prefix: Optional[str] = None) -> List[str]:
        """List keys in memory."""
        keys = list(self._storage.keys())
        if prefix:
            keys = [k for k in keys if k.startswith(prefix)]
        return sorted(keys)

    async def get_metadata(self, key: str) -> Optional[StorageMetadata]:
        """Get metadata for key."""
        return self._metadata.get(key)


class DistributedStorageManager:
    """Manager for distributed state storage with failover support."""

    def __init__(self, primary_provider: StorageProvider,
                 replica_providers: Optional[List[StorageProvider]] = None,
                 consistency_check: bool = True):
        """
        Initialize distributed storage manager.

        Args:
            primary_provider: Primary storage provider
            replica_providers: Optional list of replica providers for high availability
            consistency_check: Whether to check consistency across replicas
        """
        self._primary = primary_provider
        self._replicas = replica_providers or []
        self._consistency_check = consistency_check

    async def store(self, key: str, data: bytes, metadata: Optional[StorageMetadata] = None,
                   sync_replicas: bool = True) -> None:
        """
        Store data with optional replication.

        Args:
            key: Unique identifier for the data
            data: Data to store
            metadata: Optional metadata
            sync_replicas: Whether to sync to replica providers
        """
        # Store in primary
        await self._primary.store(key, data, metadata)

        # Store in replicas if requested
        if sync_replicas:
            for replica in self._replicas:
                try:
                    await replica.store(key, data, metadata)
                except Exception as e:
                    logger.warning(f"Failed to store in replica: {str(e)}")

    async def retrieve(self, key: str, allow_fallback: bool = True) -> bytes:
        """
        Retrieve data with fallback to replicas.

        Args:
            key: Key to retrieve
            allow_fallback: Whether to fallback to replicas on primary failure

        Returns:
            Retrieved data
        """
        # Try primary first
        try:
            return await self._primary.retrieve(key)
        except StorageError:
            if not allow_fallback or not self._replicas:
                raise

        # Try replicas
        for replica in self._replicas:
            try:
                return await replica.retrieve(key)
            except StorageError:
                continue

        raise StorageError(f"Key '{key}' not found in any storage provider")

    async def exists(self, key: str) -> bool:
        """Check if key exists in any provider."""
        # Check primary
        if await self._primary.exists(key):
            return True

        # Check replicas
        for replica in self._replicas:
            try:
                if await replica.exists(key):
                    return True
            except Exception:
                continue

        return False

    async def delete(self, key: str, sync_replicas: bool = True) -> None:
        """
        Delete data from all providers.

        Args:
            key: Key to delete
            sync_replicas: Whether to delete from replica providers
        """
        # Delete from primary
        await self._primary.delete(key)

        # Delete from replicas if requested
        if sync_replicas:
            for replica in self._replicas:
                try:
                    await replica.delete(key)
                except Exception as e:
                    logger.warning(f"Failed to delete from replica: {str(e)}")

    async def list_keys(self, prefix: Optional[str] = None) -> List[str]:
        """List keys from primary provider."""
        return await self._primary.list_keys(prefix)

    async def get_metadata(self, key: str) -> Optional[StorageMetadata]:
        """Get metadata from primary provider."""
        return await self._primary.get_metadata(key)

    async def check_consistency(self, key: str) -> Dict[str, Any]:
        """
        Check data consistency across providers.

        Args:
            key: Key to check

        Returns:
            Consistency report
        """
        report = {
            'key': key,
            'consistent': True,
            'providers': {},
            'errors': []
        }

        # Check primary
        try:
            primary_data = await self._primary.retrieve(key)
            primary_meta = await self._primary.get_metadata(key)
            report['providers']['primary'] = {
                'exists': True,
                'size': len(primary_data),
                'checksum': hashlib.sha256(primary_data).hexdigest()
            }
        except Exception as e:
            report['consistent'] = False
            report['errors'].append(f"Primary error: {str(e)}")
            report['providers']['primary'] = {'exists': False, 'error': str(e)}

        # Check replicas
        for i, replica in enumerate(self._replicas):
            provider_name = f'replica_{i}'
            try:
                replica_data = await replica.retrieve(key)
                replica_meta = await replica.get_metadata(key)
                checksum = hashlib.sha256(replica_data).hexdigest()
                report['providers'][provider_name] = {
                    'exists': True,
                    'size': len(replica_data),
                    'checksum': checksum
                }

                # Check consistency with primary
                if 'primary' in report['providers']:
                    primary_checksum = report['providers']['primary'].get('checksum')
                    if primary_checksum and checksum != primary_checksum:
                        report['consistent'] = False
                        report['errors'].append(f"Checksum mismatch: {provider_name}")

            except Exception as e:
                report['consistent'] = False
                report['errors'].append(f"{provider_name} error: {str(e)}")
                report['providers'][provider_name] = {'exists': False, 'error': str(e)}

        return report


def create_filesystem_storage(base_path: Union[str, Path]) -> DistributedStorageManager:
    """Create a filesystem-based distributed storage manager."""
    primary = FilesystemStorageProvider(base_path)
    return DistributedStorageManager(primary)


def create_memory_storage() -> DistributedStorageManager:
    """Create an in-memory distributed storage manager."""
    primary = MemoryStorageProvider()
    return DistributedStorageManager(primary)