from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, SerializeAsAny

class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: Optional[Dict[str, Any]] = None
    id: Union[str, int, None] = 1

class JsonRpcErrorData(BaseModel):
    code: int
    message: str
    data: Optional[Any] = None

class JsonRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    result: Optional[Any] = None
    error: Optional[JsonRpcErrorData] = None
    id: Union[str, int, None] = None

class RpcCommandRunParams(BaseModel):
    command: str

class RpcEventSubscribeParams(BaseModel):
    events: List[str]

class RpcEventPushParams(BaseModel):
    event: str
    subId: str
    payload: Dict[str, Any]
    ts: int

class RpcSchematicInitParams(BaseModel):
    name: str
    size: int
    hash: str

class RpcSchematicChunkParams(BaseModel):
    uploadId: str
    offset: int
    data: str  # Base64

class RpcSchematicCommitParams(BaseModel):
    uploadId: str
