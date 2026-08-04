import json
import os
from unittest.mock import patch

import pytest
from baritone_client import WebSocketTransport
from baritone_client.transport import transport as transport_module

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "transcripts.json")


def load_transcripts():
    if not os.path.exists(FIXTURE_PATH):
        return []
    with open(FIXTURE_PATH, "r") as f:
        return json.load(f)


TRANSCRIPTS = load_transcripts()


class _DummyFuture:
    """Stands in for the concurrent.futures.Future returned by the event loop."""

    def result(self, timeout=None):
        return None


@pytest.mark.parametrize("transcript", TRANSCRIPTS)
def test_golden_transcript(transcript):
    """Replay a golden transcript against WebSocketTransport.dispatch.

    Validates that dispatch:
      1. frames the request as JSON-RPC 2.0 with the expected method/params;
      2. correlates the reply by request id and returns only ``result``.

    The transport owns a websockets/asyncio connection, so the socket itself is
    replaced rather than dialled -- these are offline tests and must never open
    a real connection (an earlier version patched a ``connect`` symbol this code
    path no longer uses, so every run burned the full reconnect backoff).
    """
    req_data = transcript["request"]
    resp_data = transcript["response"]
    rpc_method = req_data["method"]
    params = req_data["params"]

    sent = []

    with patch.object(WebSocketTransport, "_connect", lambda self: None):
        transport = WebSocketTransport("ws://localhost:9090")

        def fake_send(message):
            # dispatch registers the response queue before sending, so the
            # golden reply can be delivered exactly as the read loop would.
            sent.append(message)
            reply = dict(resp_data)
            reply["id"] = message["id"]
            transport._response_queues[message["id"]].put(reply)
            return None

        # dispatch evaluates self._send_message(req) before handing it to the
        # loop, so fake_send does the work and the scheduling call is inert.
        transport._send_message = fake_send

        with patch.object(
            transport_module.asyncio,
            "run_coroutine_threadsafe",
            lambda coro, loop: _DummyFuture(),
        ):
            route = rpc_method.replace(".", "/")
            result = transport.dispatch(route, params)

        assert len(sent) == 1
        sent_dict = sent[0]
        assert sent_dict["jsonrpc"] == "2.0"
        assert sent_dict["method"] == rpc_method
        assert sent_dict["params"] == params
        assert isinstance(sent_dict["id"], int)

        # dispatch unwraps the envelope and hands back only the result payload.
        assert result == resp_data["result"]

        transport.shutdown()
