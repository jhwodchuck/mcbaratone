import unittest
from unittest.mock import MagicMock, patch
from baritone_client import Client, TransportEvent


class MockTransport:
    def __init__(self):
        self.dispatched = []
        self.events = MagicMock()
        self.event_manager = MagicMock()
        self.subscribers = {}

    def dispatch(self, route, payload, **kwargs):
        self.dispatched.append((route, payload))

        # Simulate responses based on command
        if route == "command" and payload.get("command") == "chat":
            message = payload["params"]["message"]
            if message == "#tunnel 3 3 3":
                # Simulate successful command execution
                self._emit_event(TransportEvent.MOVEMENT_STATUS, {"status": "running", "command": message})
                self._emit_event(TransportEvent.PATHFINDING_STATE, {"progress": 0.5, "command": message})
                self._emit_event(TransportEvent.MOVEMENT_STATUS, {"status": "complete", "command": message, "result": "success"})
                return {"status": "ok", "data": {"message": "Tunnel command accepted"}}
            elif message.startswith("#tunnel") and message != "#tunnel 3 3 3":
                # Invalid tunnel command
                return {"status": "error", "error": "Invalid tunnel parameters"}
            else:
                return {"status": "ok", "data": {"message": "Command accepted"}}
        return {"status": "ok", "data": payload}

    def _emit_event(self, event_type, payload):
        """Emit event to subscribers."""
        for callback in self.subscribers.get(event_type, []):
            callback(payload)

    def subscribe(self, event, callback):
        if event not in self.subscribers:
            self.subscribers[event] = []
        self.subscribers[event].append(callback)

    def shutdown(self):
        pass


class TunnelCommandTest(unittest.TestCase):
    def setUp(self):
        self.transport = MockTransport()
        self.client = Client(self.transport)

    def test_tunnel_command_dispatch(self):
        """Test that #tunnel 3 3 3 command is properly dispatched."""
        response = self.client.command.run("#tunnel 3 3 3")

        # Verify command was dispatched. CommandFacade.run always sends an
        # explicit priority alongside the message.
        expected_payload = {
            "command": "chat",
            "params": {"message": "#tunnel 3 3 3", "priority": "normal"},
        }
        self.assertIn(("command", expected_payload), self.transport.dispatched)

        # Verify response indicates success
        self.assertEqual(response["message"], "Tunnel command accepted")

    def test_tunnel_command_events(self):
        """Test that tunnel command emits appropriate events."""
        # Subscribe to events
        events_received = []

        self.client.on(TransportEvent.MOVEMENT_STATUS, lambda data: events_received.append(("movement", data)))
        self.client.on(TransportEvent.PATHFINDING_STATE, lambda data: events_received.append(("pathfinding", data)))

        # Execute command
        self.client.command.run("#tunnel 3 3 3")

        # Verify events were emitted
        self.assertTrue(len(events_received) > 0)
        # Check for running status
        running_events = [e for e in events_received if e[1].get("status") == "running"]
        self.assertTrue(len(running_events) > 0)
        # Check for progress
        progress_events = [e for e in events_received if "progress" in e[1]]
        self.assertTrue(len(progress_events) > 0)
        # Check for completion
        complete_events = [e for e in events_received if e[1].get("status") == "complete"]
        self.assertTrue(len(complete_events) > 0)

    def test_invalid_tunnel_command_error_handling(self):
        """Test error handling for invalid tunnel commands."""
        with self.assertRaises(Exception):  # CommandError from client.py
            self.client.command.run("#tunnel invalid")

    def test_tunnel_command_validation(self):
        """Test that tunnel command requires proper format."""
        # Valid command should work
        response = self.client.command.run("#tunnel 3 3 3")
        self.assertIn("message", response)
        self.assertEqual(response["message"], "Tunnel command accepted")


if __name__ == "__main__":
    unittest.main()