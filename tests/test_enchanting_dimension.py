from types import SimpleNamespace

from baritone_client.automator.phases import enchanting


class _Transport:
    def __init__(self, dimension, *, wrapped=False):
        self.dimension = dimension
        self.wrapped = wrapped

    def dispatch(self, route, _payload):
        if route == "get_state":
            state = {"dimension": self.dimension}
            return {"data": state} if self.wrapped else state
        if route == "find_blocks":
            return {"blocks": []}
        raise AssertionError(route)


class _State:
    def __init__(self, portals=None):
        self.portals = portals or []

    def get_locations(self, category):
        assert category == "nether_portal"
        return {"nether_portal": self.portals}


def test_enchanting_dimension_guard_accepts_wrapped_overworld_state(monkeypatch):
    client = SimpleNamespace(
        transport=_Transport("minecraft:overworld", wrapped=True)
    )
    handler = enchanting.EnchantingPipelineHandler()
    monkeypatch.setattr(
        enchanting,
        "find_nearest_portal",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("Overworld must not search for a portal")
        ),
    )

    assert handler._ensure_overworld(client, _State())


def test_enchanting_dimension_guard_returns_through_persisted_nether_portal(
    monkeypatch,
):
    client = SimpleNamespace(transport=_Transport("minecraft:the_nether"))
    state = _State(
        [
            {
                "x": -132,
                "y": 78,
                "z": -7,
                "dimension": "minecraft:the_nether",
            }
        ]
    )
    calls = []
    monkeypatch.setattr(enchanting, "find_nearest_portal", lambda *_args: None)

    def traverse(_client, portal, **kwargs):
        calls.append((portal, kwargs))
        client.transport.dimension = "minecraft:overworld"
        return True

    monkeypatch.setattr(
        enchanting,
        "enter_portal",
        traverse,
    )

    assert enchanting.EnchantingPipelineHandler()._ensure_overworld(client, state)
    assert calls == [
        (
            (-132, 78, -7),
            {"target_dimension": "minecraft:overworld", "timeout": 60},
        )
    ]


def test_enchanting_dimension_guard_fails_closed_without_return_portal(monkeypatch):
    client = SimpleNamespace(transport=_Transport("minecraft:the_nether"))
    monkeypatch.setattr(enchanting, "find_nearest_portal", lambda *_args: None)
    monkeypatch.setattr(
        enchanting,
        "enter_portal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not attempt an unverified portal")
        ),
    )

    assert not enchanting.EnchantingPipelineHandler()._ensure_overworld(
        client, _State()
    )


def test_enchanting_dimension_guard_rejects_false_positive_traversal(monkeypatch):
    client = SimpleNamespace(transport=_Transport("minecraft:the_nether"))
    monkeypatch.setattr(
        enchanting, "find_nearest_portal", lambda *_args: (-132, 78, -7)
    )
    monkeypatch.setattr(enchanting, "enter_portal", lambda *_args, **_kwargs: True)

    assert not enchanting.EnchantingPipelineHandler()._ensure_overworld(
        client, _State()
    )
