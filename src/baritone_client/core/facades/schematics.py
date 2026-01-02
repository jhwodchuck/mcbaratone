import base64
import hashlib
import logging
import math
import os
from typing import Dict, Any, Optional

from ...transport.transport import Transport
from ...core.exceptions import TransportError

logger = logging.getLogger(__name__)

class SchematicManager:
    """Manager for uploading and managing schematics."""
    
    def __init__(self, transport: Transport) -> None:
        """
        Initialize schematic manager.
        
        Args:
            transport: Transport instance
        """
        self.transport = transport

    def upload(self, file_path: str, name: Optional[str] = None) -> str:
        """
        Upload a schematic file to Baritone.
        
        The file is uploaded in chunks with automatic retry logic.
        SHA-256 hash is calculated for verification.
        
        Args:
            file_path: Path to the schematic file
            name: Optional name for the schematic (defaults to filename)
        
        Returns:
            File ID/name of the uploaded schematic
        
        Raises:
            FileNotFoundError: If schematic file doesn't exist
            TransportError: If upload fails or verification fails
        
        Example:
            ```python
            file_id = client.schematics.upload("house.schematic")
            client.process.builder.start(file_id, x=100, y=64, z=200)
            ```
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Schematic file not found: {file_path}")

        file_size = os.path.getsize(file_path)
        filename = name or os.path.basename(file_path)
        
        # Calculate SHA-256 hash
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        file_hash = sha256_hash.hexdigest()

        # 1. Init
        logger.info(f"Initiating upload for {filename} ({file_size} bytes)")
        init_resp = self.transport.dispatch("schematics.upload.init", {
            "name": filename,
            "size": file_size,
            "hash": file_hash
        })
        
        upload_id = init_resp["uploadId"]
        max_chunk_size = init_resp.get("maxChunkSize", 65536)
        
        # 2. Upload Chunks
        with open(file_path, "rb") as f:
            offset = 0
            while True:
                chunk = f.read(max_chunk_size)
                if not chunk:
                    break
                
                chunk_b64 = base64.b64encode(chunk).decode("utf-8")
                
                # Simple retry logic (retry once)
                try:
                    self._send_chunk(upload_id, offset, chunk_b64)
                except TransportError as e:
                    logger.warning(f"Chunk upload failed at offset {offset}, retrying: {e}")
                    self._send_chunk(upload_id, offset, chunk_b64)
                
                offset += len(chunk)
                
        # 3. Commit
        logger.info(f"Committing upload {upload_id}")
        commit_resp = self.transport.dispatch("schematics.upload.commit", {
            "uploadId": upload_id
        })
        
        if not commit_resp.get("verified"):
            raise TransportError("Upload verification failed on server side")
            
        return commit_resp.get("fileId", upload_id)

    def _send_chunk(self, upload_id: str, offset: int, data_b64: str) -> None:
        self.transport.dispatch("schematics.upload.chunk", {
            "uploadId": upload_id,
            "offset": offset,
            "data": data_b64
        })
