import time
import unittest
from unittest.mock import patch

from baritone_client.common.resources import ensure_supplies
from baritone_client.common.playbook import phase_task
from baritone_client.common.tasks import TaskResult


class FakeTransport:
    def __init__(self):
        self.inventory = {}
        self.commands = []

    def add_item(self, item_id: str, count: int) -> None:
        self.inventory[item_id] = self.inventory.get(item_id, 0) + count

    def dispatch(self, route, payload):
        if route == "get_inventory":
            items = []
            for idx, (item_id, count) in enumerate(self.inventory.items()):
                items.append({"id": item_id, "count": count, "slot": idx})
            return {"status": "ok", "data": {"inventory": items}}
        if route == "craft":
            self.commands.append(("craft", payload))
            self.add_item(payload.get("item"), payload.get("count", 1))
            return {"status": "ok"}
        if route == "mine":
            self.commands.append(("mine", payload))
            blocks = payload.get("blocks") or []
            if blocks:
                self.add_item(blocks[0], payload.get("quantity", 1))
            return {"started": True}
        return {}


class FakeClient:
    def __init__(self):
        self.transport = FakeTransport()


class FakeState:
    def __init__(self):
        self.progress_updates = []
        self.custom_data = {}

    def update_progress(self, value: float) -> None:
        self.progress_updates.append(value)


class FakeResources:
    def __init__(self, ready: bool):
        self.ready = ready
        self.refresh_calls = 0

    def refresh_inventory(self) -> None:
        self.refresh_calls += 1

    def is_phase_ready(self, phase) -> bool:
        return self.ready


class CommonHelperTests(unittest.TestCase):
    def test_ensure_supplies_uses_custom_strategy_and_reports_operations(self):
        client = FakeClient()
        client.transport.add_item("minecraft:oak_log", 1)

        strategies = {
            "minecraft:oak_log": lambda c, qty: c.transport.add_item("minecraft:oak_log", qty),
            "minecraft:crafting_table": lambda c, qty: c.transport.add_item("minecraft:crafting_table", qty),
        }

        with patch("baritone_client.common.resources.time.sleep", lambda _: None):
            result = ensure_supplies(
                client,
                {"minecraft:oak_log": 4, "minecraft:crafting_table": 1},
                strategies=strategies,
                poll_interval=0.01,
                timeout=1,
            )

        self.assertTrue(result.success)
        self.assertGreaterEqual(client.transport.inventory["minecraft:oak_log"], 4)
        self.assertEqual(client.transport.inventory["minecraft:crafting_table"], 1)
        self.assertGreaterEqual(len(result.data.get("operations", [])), 1)

    def test_phase_task_skips_when_requirements_met(self):
        state = FakeState()
        resources = FakeResources(ready=True)
        task = phase_task(
            "Skip ready phase",
            lambda client, ctx: TaskResult.ok("noop"),
            state,
            resources,
            phase="BOOTSTRAP",
        )

        result = task.run(FakeClient())
        self.assertTrue(result.success)
        self.assertTrue(result.data.get("skipped"))
        self.assertEqual(resources.refresh_calls, 1)

    def test_phase_task_executes_handler_and_persists_payload(self):
        state = FakeState()
        resources = FakeResources(ready=False)

        def executor(client, ctx):
            ctx.metadata["foo"] = "bar"
            return TaskResult.ok("done", pearls=12)

        task = phase_task("Execute phase", executor, state, resources, phase="ENDER")
        result = task.run(FakeClient())

        self.assertTrue(result.success)
        self.assertIn("phase_payloads", state.custom_data)
        self.assertEqual(state.custom_data["phase_payloads"]["ENDER"]["pearls"], 12)
        self.assertGreaterEqual(len(state.progress_updates), 1)


if __name__ == "__main__":
    unittest.main()
