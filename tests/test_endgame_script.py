import unittest

from baritone_client.enums import TransportEvent
from baritone_client.scripts.endgame import EndGameMission, MissionPhase, MissionState


class FakeMissionFacade:
    def __init__(self):
        self.macro_calls = []
        self.queue_calls = []
        self.checkpoint_calls = []
        self.status_payload = {
            "mission": {
                "phase": MissionPhase.IDLE.value,
                "note": "not_started",
                "telemetry": {},
                "queue": [],
                "history": [],
            }
        }

    def status(self):
        return self.status_payload

    def macro(self, name=None, params=None, dequeue=False):
        self.macro_calls.append({"name": name, "params": params or {}, "dequeue": dequeue})
        if name:
            self.status_payload["mission"]["phase"] = name
            self.status_payload["mission"]["history"].append(name)
        return {"macro": name}

    def queue(self, actions, clear=False):
        self.queue_calls.append({"actions": actions, "clear": clear})
        self.status_payload["mission"]["queue"] = list(actions)
        return {"queued": len(actions)}

    def checkpoint(self, phase, note=None):
        self.checkpoint_calls.append({"phase": phase, "note": note})
        return {"phase": phase, "note": note}


class FakeClient:
    def __init__(self):
        self.mission = FakeMissionFacade()
        self.subscriptions = []

    def on(self, event, callback):
        self.subscriptions.append((event, callback))


class EndGameMissionTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.state = MissionState()
        self.mission = EndGameMission(self.client, state=self.state)

    def test_synchronize_updates_phase_and_history(self):
        payload = {
            "mission": {
                "phase": "bootstrap",
                "note": "Activated",
                "telemetry": {"health": 20},
                "queue": ["establish_base"],
                "history": ["bootstrap"],
            }
        }
        self.client.mission.status_payload = payload
        result = self.mission.synchronize()
        self.assertEqual(MissionPhase.BOOTSTRAP, result.phase)
        self.assertEqual(["establish_base"], result.queue)
        self.assertEqual(["bootstrap"], result.history)
        self.assertEqual("Activated", result.note)

    def test_bootstrap_invokes_macro_and_records_checkpoint(self):
        self.mission.bootstrap_world(settings={"allowSprint": True})
        self.assertIn("bootstrap", self.state.checkpoints)
        self.assertEqual("bootstrap", self.client.mission.macro_calls[0]["name"])
        self.assertIn(TransportEvent.CHAT, [evt for evt, _ in self.client.subscriptions])

    def test_run_full_mission_sequences_macros(self):
        self.mission.run_full_mission()
        macro_order = [call["name"] for call in self.client.mission.macro_calls]
        self.assertEqual(
            ["bootstrap", "establish_base", "resource_pipeline", "enter_nether", "craft_eyes", "locate_stronghold", "fight_dragon"],
            macro_order,
        )
        self.assertIn("final_battle", self.state.checkpoints)

    def test_schedule_actions_calls_queue(self):
        self.mission.schedule_actions(["bootstrap", "establish_base"], clear_existing=True)
        self.assertEqual(1, len(self.client.mission.queue_calls))
        call = self.client.mission.queue_calls[0]
        self.assertTrue(call["clear"])
        self.assertEqual(["bootstrap", "establish_base"], call["actions"])


if __name__ == "__main__":
    unittest.main()
