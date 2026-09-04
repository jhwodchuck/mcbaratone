"""Explicitly authorized, disposable admin-arena acceptance; never A1/lab Survival.

Run with --allow-mutations --artifact-sha256 HASH. This script checks identity
before any fixture command. Admin-supplied fixtures are not mission progress.
"""
import argparse
import json
import socket
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from scripts.maintenance.locate_biome_atlas import RconClient
from baritone_client.common import nether


class Wire:
    def __init__(self):
        self.socket = socket.create_connection(("127.0.0.1", 5695), 8)
        self.socket.settimeout(12)
        self.reader = self.socket.makefile("r")
        self.seq = 0
        self.session = uuid.uuid4().hex

    def response(self, route, payload, request_id=None):
        self.seq += 1
        request_id = request_id or self.session + str(self.seq)
        request = {"id": request_id, "request_seq": self.seq, "command": route, "params": payload}
        self.socket.sendall((json.dumps(request) + "\n").encode())
        while True:
            response = json.loads(self.reader.readline())
            if response.get("id") == request_id:
                assert response.get("request_seq") == self.seq, "sequence echo missing"
                return response

    def dispatch(self, route, payload, **kwargs):
        response = self.response(route, payload)
        if response.get("status") != "ok":
            raise RuntimeError(f"{route}: {response.get('error')} {response.get('data')}")
        return response["data"]


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.2)
    raise AssertionError("Observed postcondition deadline exceeded")


def run(expected_hash):
    wire = Wire()
    state = wire.dispatch("get_state", {})
    assert state.get("player_name") == "FunctionalAdmin"
    assert state.get("world_identity", {}).get("server_address") == "localhost:25580"
    version = wire.dispatch("get_version", {})
    assert version.get("artifact_sha256", "").lower() == expected_hash.lower()
    assert version.get("capabilities", {}).get("observed_mutations_v1") is True
    print(json.dumps({"event": "acceptance_identity", "version": version, "world": state["world_identity"]}), flush=True)
    profile = json.loads((ROOT / "scripts/functional_world_profiles.json").read_text())["admin"]
    with RconClient("127.0.0.1", profile["rcon_port"], profile["rcon_password"], 8) as admin:
        def fixture(command):
            admin.command(command)

        def block(x, y, z):
            return wire.dispatch("get_block", {"x": x, "y": y, "z": z}).get("id")

        def hold(item, count=1):
            fixture(f"item replace entity FunctionalAdmin hotbar.0 with {item} {count}")
            wire.dispatch("select_slot", {"slot": 0})
            wait_for(lambda: wire.dispatch("get_inventory", {})["inventory"][0]["id"] == item)

        def positioned(x, y, z):
            fixture(f"tp FunctionalAdmin {x} {y} {z}")
            wait_for(lambda: abs(wire.dispatch("get_state", {})["position"]["x"] - x) < .6)

        fixture("gamemode creative FunctionalAdmin")
        positioned(102.5, 121, 103.5)
        fixture("fill 96 120 96 112 120 112 minecraft:stone")
        fixture("fill 96 121 96 112 127 112 minecraft:air")
        fixture("gamemode survival FunctionalAdmin")
        fixture("clear FunctionalAdmin")
        wait_for(lambda: block(102, 120, 100) == "minecraft:stone")
        wait_for(lambda: wire.dispatch("get_state", {})["game_mode"] == "survival")

        hold("minecraft:cobblestone", 2)
        placement = {"x": 102, "y": 121, "z": 100, "block": "minecraft:cobblestone"}
        placement_id = wire.session + "-dedup-placement"
        placed_response = wire.response("place_block", placement, request_id=placement_id)
        assert placed_response["status"] == "ok"
        placed = placed_response["data"]
        assert placed.get("placed") is True and placed.get("postcondition_verified") is True
        assert block(102, 121, 100) == "minecraft:cobblestone"
        assert wire.dispatch("get_inventory", {})["inventory"][0]["count"] == 1
        replay = wire.response("place_block", placement, request_id=placement_id)
        assert replay["status"] == "ok" and replay.get("mutation_replayed") is True
        assert wire.dispatch("get_inventory", {})["inventory"][0]["count"] == 1
        mismatch = wire.response("place_block", {"x": 103, "y": 121, "z": 100, "block": "minecraft:obsidian"})
        assert mismatch["status"] == "error" and block(103, 121, 100) == "minecraft:air"
        print(json.dumps({"event": "placement_passed", "response": placed}), flush=True)

        fixture("setblock 104 120 101 minecraft:lava[level=0]")
        fixture("setblock 105 121 101 minecraft:stone")
        positioned(103.5, 121, 103.5)
        hold("minecraft:water_bucket")
        water = wire.dispatch("use_bucket", {"x": 104, "y": 121, "z": 101, "operation": "place"})
        assert water.get("postcondition_verified") is True
        wait_for(lambda: block(104, 120, 101) == "minecraft:obsidian")
        reclaimed = wire.dispatch("use_bucket", {"x": 104, "y": 121, "z": 101, "operation": "pickup"})
        assert reclaimed.get("postcondition_verified") is True
        assert wire.dispatch("get_inventory", {})["inventory"][0]["id"] == "minecraft:water_bucket"
        print(json.dumps({"event": "casting_passed", "pour": water, "reclaim": reclaimed}), flush=True)

        # The real controller builder must construct ten obsidian plus supports.
        fixture("clear FunctionalAdmin")
        fixture("give FunctionalAdmin minecraft:obsidian 10")
        fixture("give FunctionalAdmin minecraft:cobblestone 4")
        fixture("give FunctionalAdmin minecraft:flint_and_steel 1")
        positioned(101.5, 121, 105.5)
        client = SimpleNamespace(transport=wire, mission=SimpleNamespace(checkpoint=lambda *args: None))
        assert nether.build_nether_portal(client, 100, 121, 107)
        assert nether.ignite_portal(client, (100, 121, 107))
        assert nether.verify_portal(client, (100, 121, 107), require_active=True)
        print(json.dumps({"event": "portal_passed", "frame_blocks": 10, "active_interior_blocks": 6}), flush=True)

        fixture("setblock 108 121 103 minecraft:furnace")
        positioned(108.5, 121, 105.5)
        fixture("clear FunctionalAdmin")
        fixture("give FunctionalAdmin minecraft:raw_iron 1")
        fixture("give FunctionalAdmin minecraft:oak_log 2")
        wire.dispatch("interact_block", {"x": 108, "y": 121, "z": 103})
        wait_for(lambda: wire.dispatch("get_screen", {}).get("type") == "FurnaceMenu")
        screen = wire.dispatch("get_screen", {})
        slots = {entry["id"]: entry["slot"] for entry in screen["slots"] if entry.get("count", 0) > 0}
        moved = wire.dispatch("smelt_items", {"input_slot": slots["minecraft:raw_iron"], "fuel_slot": slots["minecraft:oak_log"], "sync_id": screen["sync_id"]})
        assert moved.get("moved") is True and moved.get("postcondition_verified") is True
        wait_for(lambda: wire.dispatch("get_screen", {})["slots"][2].get("id") == "minecraft:iron_ingot", 20)
        collected = wire.dispatch("inventory_click", {"slot": 2, "type": "QUICK_MOVE", "sync_id": screen["sync_id"]})
        assert collected.get("postcondition_verified") is True
        wait_for(lambda: any(i["id"] == "minecraft:iron_ingot" and i["count"] >= 1 for i in wire.dispatch("get_inventory", {})["inventory"]))
        wire.dispatch("close_screen", {})
        print(json.dumps({"event": "furnace_passed", "loaded": moved, "collected": True}), flush=True)
    print(json.dumps({"event": "acceptance_complete", "artifact_sha256": expected_hash}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-mutations", action="store_true", required=True)
    parser.add_argument("--artifact-sha256", required=True)
    args = parser.parse_args()
    run(args.artifact_sha256)
