from copy import deepcopy
from types import SimpleNamespace

import baritone_client.automator.phases.trading as trading_module
from baritone_client.automator.phases.iron_farm import IronFarmHandler
from baritone_client.automator.phases.trading import (
    REQUIRED_ENCHANTMENTS,
    ToolPerfectionHandler,
)
from baritone_client.automator.resource_manager import ResourceManager
from baritone_client.automator.state_manager import Phase


class RecordingState:
    def __init__(self, payloads=None):
        self.payloads = payloads or {}

    def get_phase_payload(self, phase):
        return self.payloads.get(phase, {})

    def record_phase_payload(self, phase, payload):
        self.payloads[phase] = payload


def entity(entity_id, entity_type, position, **extra):
    return {
        "id": entity_id,
        "type": entity_type,
        "position": {"x": position[0], "y": position[1], "z": position[2]},
        "distance": extra.pop("distance", 3.0),
        **extra,
    }


def swap_inventory_slots(inventory, source_slot, target_slot):
    """Model the verified player's logical-slot SWAP contract."""
    source = next(
        (item for item in inventory if int(item.get("slot", -1)) == source_slot),
        None,
    )
    target = next(
        (item for item in inventory if int(item.get("slot", -1)) == target_slot),
        None,
    )
    if source is None:
        return
    inventory.remove(source)
    if target is not None:
        inventory.remove(target)
        inventory.append({**target, "slot": source_slot})
    inventory.append({**source, "slot": target_slot})


class IronTransport:
    def __init__(self, *, fail_transport=False):
        self.fail_transport = fail_transport
        self.calls = []
        self.blocks = {}
        self.selected = None
        self.player_position = {"x": 10, "y": 65, "z": 10}
        self.entities = [
            entity(1, "minecraft:villager", (0, 65, 0), is_baby=False),
            entity(2, "minecraft:villager", (2, 65, 0), is_baby=False),
            entity(3, "minecraft:villager", (4, 65, 0), is_baby=False),
            entity(4, "minecraft:zombie", (6, 65, 0)),
            entity(5, "minecraft:iron_golem", (11, 65, 10)),
        ]
        item_counts = {
            "minecraft:cobblestone": 160,
            "minecraft:white_bed": 3,
            "minecraft:hopper": 1,
            "minecraft:chest": 1,
            "minecraft:water_bucket": 1,
            "minecraft:lava_bucket": 1,
            "minecraft:oak_boat": 1,
        }
        self.inventory = [
            {"slot": slot, "id": item_id, "count": count}
            for slot, (item_id, count) in enumerate(item_counts.items())
        ]

    def _target(self, target_id):
        return next(value for value in self.entities if value["id"] == target_id)

    def dispatch(self, route, payload, **_kwargs):
        self.calls.append((route, deepcopy(payload)))
        if route == "get_state":
            return {
                "block_position": dict(self.player_position),
                "health": 20,
                "food_level": 20,
                "is_dead": False,
                "dimension": "minecraft:overworld",
            }
        if route == "goto":
            self.player_position = {
                "x": payload["x"],
                "y": payload["y"],
                "z": payload["z"],
            }
            return {"started": True}
        if route == "cancel":
            return {"cancelled": True}
        if route == "get_entities":
            return {"entities": deepcopy(self.entities), "count": len(self.entities)}
        if route == "get_inventory":
            return {
                "inventory": deepcopy(self.inventory),
                "armor": [],
                "offhand": [],
                "selected_slot": self.selected,
            }
        if route == "get_block":
            position = (payload["x"], payload["y"], payload["z"])
            return {"id": self.blocks.get(position, "minecraft:air")}
        if route == "select_slot":
            self.selected = int(payload["slot"])
            return {"selected": self.selected}
        if route == "place_block":
            position = (payload["x"], payload["y"], payload["z"])
            item_id = str(payload.get("block") or payload.get("item"))
            placed = {
                "minecraft:white_bed": "minecraft:white_bed",
                "minecraft:water_bucket": "minecraft:water",
                "minecraft:lava_bucket": "minecraft:lava",
            }.get(item_id, item_id)
            self.blocks[position] = placed
            return {"placed": True, "block": placed}
        if route == "entity_transport":
            action = payload["action"]
            if action in {"capture", "status"}:
                return {
                    "success": True,
                    "target_entity_id": payload["entity_id"],
                    "vehicle_entity_id": 100 + payload["entity_id"],
                    "passenger_verified": True,
                }
            if action == "transport":
                if self.fail_transport:
                    return {"success": False, "passenger_verified": False}
                target = self._target(payload["entity_id"])
                target["position"] = dict(payload["destination"])
                return {"success": True, "passenger_verified": True}
            if action == "release":
                return {"success": True, "passenger_verified": False}
        raise AssertionError(f"unexpected route: {route}")


class TradingTransport:
    def __init__(self):
        self.calls = []
        self.player_position = {"x": 0, "y": 65, "z": 0}
        self.screen = ""
        self.current_villager = None
        self.selected_trade = None
        self.selected = None
        self.blocks = {(3, 64, 0): "minecraft:anvil"}
        self.cursor = None
        self.anvil_inputs = {0: None, 1: None}
        self.next_slot = 20
        self.inventory = [
            {
                "slot": 10,
                "id": "minecraft:diamond_pickaxe",
                "count": 1,
                "enchantments": [],
            },
            {"slot": 11, "id": "minecraft:lectern", "count": 4},
            {"slot": 12, "id": "minecraft:emerald", "count": 128},
            {"slot": 13, "id": "minecraft:book", "count": 8},
        ]
        names = sorted(REQUIRED_ENCHANTMENTS)
        self.entities = [
            entity(
                index + 1,
                "minecraft:villager",
                (index * 2, 65, 0),
                profession="minecraft:librarian",
                offers_count=2,
                is_baby=False,
            )
            for index in range(4)
        ]
        self.offers = {
            index + 1: [
                {
                    "index": 0,
                    "disabled": False,
                    "uses": 0,
                    "max_uses": 12,
                    "cost_a": {"id": "minecraft:emerald", "count": 12},
                    "cost_b": {"id": "minecraft:book", "count": 1},
                    "result": {
                        "id": "minecraft:enchanted_book",
                        "count": 1,
                        "enchantments": [
                            {"id": f"minecraft:{names[index]}", "level": 1}
                        ],
                    },
                }
            ]
            for index in range(4)
        }

    def _merchant_screen(self):
        return {
            "type": "MerchantMenu",
            "slots": [],
            "merchant_offers": deepcopy(self.offers[self.current_villager]),
        }

    def _anvil_slots(self):
        slots = [dict(item) for item in self.inventory]
        for index in (0, 1):
            item = self.anvil_inputs[index]
            slots.append(
                {"slot": index, "id": "minecraft:air", "count": 0}
                if item is None
                else {**item, "slot": index}
            )
        tool = self.anvil_inputs[0]
        book = self.anvil_inputs[1]
        if tool and book:
            combined = deepcopy(tool)
            combined["enchantments"] = list(tool.get("enchantments", [])) + list(
                book.get("enchantments", [])
            )
            slots.append({**combined, "slot": 2})
        else:
            slots.append({"slot": 2, "id": "minecraft:air", "count": 0})
        return slots

    def _remove_slot(self, slot):
        item = next(item for item in self.inventory if item["slot"] == slot)
        self.inventory.remove(item)
        return item

    def dispatch(self, route, payload, **_kwargs):
        self.calls.append((route, deepcopy(payload)))
        if route == "get_state":
            return {
                "block_position": dict(self.player_position),
                "health": 20,
                "food_level": 20,
                "is_dead": False,
                "dimension": "minecraft:overworld",
            }
        if route == "goto":
            self.player_position = {"x": payload["x"], "y": payload["y"], "z": payload["z"]}
            return {"started": True}
        if route == "cancel":
            return {"cancelled": True}
        if route == "get_entities":
            return {"entities": deepcopy(self.entities), "count": len(self.entities)}
        if route == "get_inventory":
            return {
                "inventory": deepcopy(self.inventory),
                "armor": [],
                "offhand": [],
                "selected_slot": self.selected,
            }
        if route == "get_block":
            position = (payload["x"], payload["y"], payload["z"])
            return {"id": self.blocks.get(position, "minecraft:air")}
        if route == "place_block":
            position = (payload["x"], payload["y"], payload["z"])
            self.blocks[position] = str(payload["block"])
            return {"placed": True}
        if route == "select_slot":
            self.selected = int(payload["slot"])
            return {"selected": self.selected}
        if route == "break_block":
            position = (payload["x"], payload["y"], payload["z"])
            self.blocks[position] = "minecraft:air"
            return {"broken": True}
        if route == "entity_interact":
            self.current_villager = payload["entity_id"]
            self.screen = "merchant"
            return {"success": True, "accepted": True}
        if route == "get_screen":
            if self.screen == "merchant":
                return self._merchant_screen()
            if self.screen == "anvil":
                return {"type": "AnvilMenu", "slots": self._anvil_slots()}
            return {"type": "InventoryMenu", "slots": []}
        if route == "select_trade":
            self.selected_trade = int(payload["index"])
            offer = self.offers[self.current_villager][self.selected_trade]
            assert payload.get("count") == 1
            uses_before = offer["uses"]
            offer["uses"] += 1
            book = deepcopy(offer["result"])
            book["slot"] = self.next_slot
            self.next_slot += 1
            self.inventory.append(book)
            return {
                "success": True,
                "selected_index": self.selected_trade,
                "completed_count": 1,
                "uses_before": uses_before,
                "uses_after": offer["uses"],
                "selected_offer": deepcopy(offer),
                "acquired_results": [deepcopy(offer["result"])],
            }
        if route == "inventory_click":
            slot = int(payload["slot"])
            mode = payload["type"]
            if mode == "SWAP":
                swap_inventory_slots(self.inventory, slot, int(payload["button"]))
                return {"clicked": True}
            if self.screen == "merchant":
                raise AssertionError("select_trade already executes the merchant purchase")
            if self.screen == "anvil" and mode == "PICKUP":
                if slot in (0, 1):
                    self.anvil_inputs[slot] = self.cursor
                    self.cursor = None
                else:
                    self.cursor = self._remove_slot(slot)
                return {"clicked": True}
            if self.screen == "anvil" and slot == 2 and mode == "QUICK_MOVE":
                tool = deepcopy(self.anvil_inputs[0])
                tool["enchantments"] = list(tool.get("enchantments", [])) + list(
                    self.anvil_inputs[1].get("enchantments", [])
                )
                tool["slot"] = 10
                self.inventory.append(tool)
                self.anvil_inputs = {0: None, 1: None}
                return {"clicked": True}
            # Native hotbar swaps used by robust_place are not part of the
            # merchant/anvil contract under test. Accept them while leaving
            # the fake's summarized inventory unchanged.
            return {"clicked": True}
        if route == "close_screen":
            self.screen = ""
            return {"closed": True}
        if route == "find_blocks":
            return {"found": [{"x": 3, "y": 64, "z": 0, "distance": 3.0}]}
        if route == "interact_block":
            self.screen = "anvil"
            return {"interacted": True}
        raise AssertionError(f"unexpected route: {route}")


def test_resource_contracts_do_not_short_circuit_world_state_phases():
    transport = IronTransport()
    resources = ResourceManager(SimpleNamespace(transport=transport))
    assert resources.get_phase_requirements(Phase.IRON_FARM) == {}
    assert resources.get_phase_requirements(Phase.TOOL_PERFECTION) == {}
    assert resources.phase_ready_result(Phase.IRON_FARM, "skip") is None
    assert resources.phase_ready_result(Phase.TOOL_PERFECTION, "skip") is None


def test_iron_farm_builds_and_physically_transports_population(monkeypatch):
    monkeypatch.setattr("baritone_client.common.harness_ops.available", lambda: False)
    monkeypatch.setattr(
        "baritone_client.common.navigation.run_navigation_defense",
        lambda *_args, **_kwargs: False,
    )
    transport = IronTransport()
    state = RecordingState({Phase.IRON_FARM: {"farm_location": [10, 64, 10]}})

    result = IronFarmHandler().execute(SimpleNamespace(transport=transport), object(), state)

    assert result.success
    assert result.data["structure_verified"]
    assert result.data["villagers_verified"]
    assert result.data["zombie_verified"]
    assert result.data["production_verified"]
    actions = [
        payload["action"]
        for route, payload in transport.calls
        if route == "entity_transport"
    ]
    assert actions == [
        "capture", "status", "transport", "release",
        "capture", "status", "transport", "release",
        "capture", "status", "transport", "release",
        "capture", "status", "transport", "status",
    ]
    transport_calls = [
        payload for route, payload in transport.calls if route == "entity_transport"
    ]
    assert all(payload["vehicle_type"] == "boat" for payload in transport_calls)
    assert all(payload["max_distance"] == 6.0 for payload in transport_calls)
    assert all("entity_id" in payload for payload in transport_calls)
    assert all("target_entity_id" not in payload for payload in transport_calls)
    zombie_calls = [
        payload for payload in transport_calls if payload["entity_id"] == 4
    ]
    assert "release" not in {payload["action"] for payload in zombie_calls}
    assert zombie_calls[-1]["action"] == "status"
    assert all(
        payload["destination"] == {"x": 10, "y": 65, "z": 10}
        for payload in transport_calls
        if payload["action"] == "transport"
    )
    assert len([1 for route, _payload in transport.calls if route == "goto"]) >= 6
    witnesses = result.data["structure_witnesses"]
    assert transport.blocks[tuple(witnesses["water_source"])] == "minecraft:water"
    assert transport.blocks[tuple(witnesses["lava_source"])] == "minecraft:lava"
    fluid_calls = [
        payload
        for route, payload in transport.calls
        if route == "place_block" and payload.get("item", "").endswith("_bucket")
    ]
    assert {payload["item"] for payload in fluid_calls} == {
        "minecraft:water_bucket",
        "minecraft:lava_bucket",
    }


def test_iron_farm_fails_when_bridge_does_not_verify_physical_transport(monkeypatch):
    monkeypatch.setattr("baritone_client.common.harness_ops.available", lambda: False)
    monkeypatch.setattr(
        "baritone_client.common.navigation.run_navigation_defense",
        lambda *_args, **_kwargs: False,
    )
    transport = IronTransport(fail_transport=True)
    state = RecordingState({Phase.IRON_FARM: {"farm_location": [10, 64, 10]}})

    result = IronFarmHandler().execute(SimpleNamespace(transport=transport), object(), state)

    assert not result.success
    assert "bridge did not verify physical passenger transport" in result.reason
    assert result.data["entity_transports"][0]["success"] is False


def test_tool_perfection_rolls_trades_and_verifies_final_tool(monkeypatch):
    monkeypatch.setattr(trading_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr("baritone_client.common.harness_ops.available", lambda: False)
    transport = TradingTransport()
    client = SimpleNamespace(transport=transport)
    state = RecordingState()

    result = ToolPerfectionHandler().execute(client, object(), state)

    assert result.success
    assert result.data["tool_perfected"]
    assert set(result.data["verified_enchantments"]) == REQUIRED_ENCHANTMENTS
    assert len(result.data["trades"]) == 4
    assert len(result.data["anvil_applications"]) == 4
    routes = [route for route, _payload in transport.calls]
    assert routes.count("select_trade") == 4
    assert not any(
        route == "inventory_click"
        and payload.get("slot") == 2
        and payload.get("type") == "QUICK_MOVE"
        and index < routes.index("interact_block")
        for index, (route, payload) in enumerate(transport.calls)
    )
    assert routes.count("break_block") == 0
    assert all(trade["purchase"]["book_observed"] for trade in result.data["trades"])
    selection = result.data["trades"][0]["purchase"]["selection"]
    assert {
        "selected_index",
        "completed_count",
        "uses_before",
        "uses_after",
        "selected_offer",
        "acquired_results",
    }.issubset(selection)


def test_offer_purchase_fails_closed_without_observed_book(monkeypatch):
    monkeypatch.setattr(trading_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr("baritone_client.common.harness_ops.available", lambda: False)
    transport = TradingTransport()
    original = transport.dispatch

    def dispatch_without_purchase(route, payload, **kwargs):
        if route == "select_trade":
            transport.calls.append((route, deepcopy(payload)))
            offer = deepcopy(transport.offers[transport.current_villager][payload["index"]])
            return {
                "success": True,
                "selected_index": payload["index"],
                "completed_count": 0,
                "uses_before": offer["uses"],
                "uses_after": offer["uses"],
                "selected_offer": offer,
                "acquired_results": [],
            }
        return original(route, payload, **kwargs)

    transport.dispatch = dispatch_without_purchase
    result = ToolPerfectionHandler().execute(
        SimpleNamespace(transport=transport),
        object(),
        RecordingState(),
    )

    assert not result.success
    assert "did not produce a verified book" in result.reason


def test_librarian_candidates_exclude_nitwits():
    candidates = trading_module._villager_candidates(
        [
            entity(1, "minecraft:villager", (0, 64, 0), profession="minecraft:nitwit"),
            entity(2, "minecraft:villager", (1, 64, 0), profession="minecraft:none"),
        ]
    )
    assert [candidate["id"] for candidate in candidates] == [2]


def test_tool_metadata_is_not_combined_across_multiple_pickaxes():
    observed = trading_module._best_item_enchantments(
        [
            {
                "id": "minecraft:diamond_pickaxe",
                "enchantments": ["minecraft:mending", "minecraft:efficiency"],
            },
            {
                "id": "minecraft:netherite_pickaxe",
                "enchantments": ["minecraft:fortune", "minecraft:unbreaking"],
            },
        ],
        item_ids=trading_module.TOOL_IDS,
    )
    assert observed in ({"mending", "efficiency"}, {"fortune", "unbreaking"})
    assert observed != REQUIRED_ENCHANTMENTS
