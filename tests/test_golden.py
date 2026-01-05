import json
import os
from unittest.mock import MagicMock, patch

import pytest
from baritone_client import WebSocketTransport
# Note: JsonRpcRequest/JsonRpcResponse are internal to transport, not needed for this test

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "transcripts.json")

def load_transcripts():
    if not os.path.exists(FIXTURE_PATH):
        return []
    with open(FIXTURE_PATH, "r") as f:
        return json.load(f)

TRANSCRIPTS = load_transcripts()

@pytest.mark.parametrize("transcript", TRANSCRIPTS)
def test_golden_transcript(transcript):
    """
    Replay a golden transcript against the WebSocketTransport.
    validates that:
    1. Transport sends the expected JSON-RPC Request.
    2. Transport returns the expected Result from the Response.
    """
    req_data = transcript["request"]
    resp_data = transcript["response"]
    rpc_method = req_data["method"]
    params = req_data["params"]
    
    # Setup Transport with Mock Socket
    with patch("baritone_client.transport.connect") as mock_connect:
        mock_socket = MagicMock()
        mock_connect.return_value = mock_socket
        
        # Fix response ID to match what the transport will likely generate (1 for first request)
        # Since we create a new transport per test, it always starts at 1
        resp_data_fixed = resp_data.copy()
        resp_data_fixed["id"] = 1
        
        # Configure socket to return the golden response ONCE, then simulate idle/disconnect
        # This prevents the _read_loop from spinning infinitely on the same response
        mock_socket.recv.side_effect = [json.dumps(resp_data_fixed), Exception("End of stream")]
        
        transport = WebSocketTransport("ws://localhost:9090")
        
        # Determine strict route from method name (reverse mapping)
        # In transport.py we map "command/run" -> "commands.run"
        # For this test, we can just pass the method name if we bypass the client facade,
        # OR we can assume the transport dispatch accepts dotted notation fallback
        # based on `rpc_method = method_map.get(route, route.replace("/", "."))`
        
        # Execute dispatch
        # We use the method name directly as the route to test the generic dispatch
        # capability, or we map it inverted if needed.
        # Let's trust route.replace("/", ".") behavior for "commands.run" -> "commands/run"
        route = rpc_method.replace(".", "/")
        
        result = transport.dispatch(route, params)
        
        # Verify Request
        # The transport should have called socket.send with the JSON request
        assert mock_socket.send.call_count == 1
        args, _ = mock_socket.send.call_args
        sent_json = args[0]
        sent_dict = json.loads(sent_json)
        
        # Verify fields (excluding ID which is auto-incremented/random)
        assert sent_dict["jsonrpc"] == "2.0"
        assert sent_dict["method"] == rpc_method
        assert sent_dict["params"] == params
        
        # Verify Result
        assert result == resp_data["result"]
        
        transport.shutdown()
