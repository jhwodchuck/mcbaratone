import sys
from unittest import TestCase
from unittest.mock import MagicMock

from baritone_client.client import Client
from baritone_client.enums import MovementStatus, PathCalculationResultType, PathingCommandType
from baritone_client.goals import GoalFactory
from baritone_client.models import Selection
from baritone_client.processes import BuilderProcess
from baritone_client.serialization import better_block_pos


class DummyTransport:
    def __init__(self):
        self.dispatched = []
        self.events = MagicMock()

    def dispatch(self, route, payload, **kwargs):
        self.dispatched.append((route, payload))
        return {"route": route, "payload": payload}

    def subscribe(self, event, callback):
        self.events.subscribe(event, callback)

    def emit(self, event, payload):
        self.events.dispatch(event, payload)

    def shutdown(self):
        self.dispatched.append(("shutdown", {}))


class ClientFacadeTest(TestCase):
    def setUp(self):
        self.transport = DummyTransport()
        self.client = Client(self.transport)

    def test_command_dispatch(self):
        response = self.client.command.run("goto 1 64 1")
        expected_payload = {"command": "chat", "params": {"message": "goto 1 64 1"}}
        self.assertIn(("command", expected_payload), self.transport.dispatched)
        self.assertEqual(expected_payload, response)

    def test_goal_serialization_and_apply(self):
        goal = GoalFactory.goal_block(1, 2, 3)
        response = self.client.goals.apply(goal)
        self.assertEqual("goal/apply", response["route"])
        self.assertEqual(goal.to_dict(), response["payload"])

    def test_settings_validation(self):
        response = self.client.settings.set("allowSprint", True)
        self.assertEqual({"name": "allowSprint", "value": True}, response["payload"])

    def test_builder_process_selection(self):
        selection = Selection(start=better_block_pos(0, 64, 0), end=better_block_pos(1, 65, 1))
        builder = BuilderProcess(self.transport)
        result = builder.start("house", selection=selection)
        self.assertEqual("process/builder/start", result["route"])
        self.assertIn("selection", result["payload"])
        self.assertEqual({"start": {"x": 0, "y": 64, "z": 0}, "end": {"x": 1, "y": 65, "z": 1}}, result["payload"]["selection"])

    def test_process_helpers(self):
        self.client.process.movement_status(MovementStatus.RUNNING)
        self.client.process.calculation_result(PathCalculationResultType.SUCCESS)
        self.assertIn(("process/status", {"status": MovementStatus.RUNNING.value}), self.transport.dispatched)
        self.assertIn(("process/path_result", {"result": PathCalculationResultType.SUCCESS.value}), self.transport.dispatched)

    def test_shutdown(self):
        self.client.shutdown()
        self.assertIn(("shutdown", {}), self.transport.dispatched)


class EnumSerializationTest(TestCase):
    def test_pathing_command_type_values(self):
        self.assertEqual("request_pause", PathingCommandType.REQUEST_PAUSE.value)
        self.assertEqual("move_to", PathingCommandType.MOVE_TO.value)
