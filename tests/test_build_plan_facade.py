from typing import Any, Dict, List, Tuple

from baritone_client.core.facades.schematics import SchematicManager


class DummyTransport:
    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def dispatch(self, route: str, payload: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        self.calls.append((route, payload))
        return {}


def _sample_block_plan() -> Dict[str, Any]:
    return {
        "version": 2,
        "blocks": [
            {"pos": [1, 2, 3], "block": "stone"},
        ],
    }


def test_preview_build_plan_v2_direct_payload_does_not_dispatch():
    transport = DummyTransport()
    manager = SchematicManager(transport)

    preview = manager.preview_build_plan_v2(_sample_block_plan())

    assert preview.valid
    assert preview.valid_count == 1
    assert preview.commands == ["setblock 1 2 3 minecraft:stone"]
    assert transport.calls == []


def test_preview_build_plan_v2_wrapped_payload_does_not_dispatch():
    transport = DummyTransport()
    manager = SchematicManager(transport)

    preview = manager.preview_build_plan_v2({"build_plan": _sample_block_plan()})

    assert preview.valid
    assert preview.valid_count == 1
    assert preview.commands == ["setblock 1 2 3 minecraft:stone"]
    assert transport.calls == []
