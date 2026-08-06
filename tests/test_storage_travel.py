from types import SimpleNamespace

from baritone_client.common import storage_safety


class _StateTransport:
    def __init__(self, positions):
        self._positions = iter(positions)

    def dispatch(self, route, payload=None):
        assert route == "get_state"
        x, y, z = next(self._positions)
        return {"block_position": {"x": x, "y": y, "z": z}}


def test_chunk_loader_accepts_navigation_stopping_inside_interaction_range():
    client = SimpleNamespace(
        transport=_StateTransport([(10.0, 64.0, 0.0), (3.2, 64.0, 0.0)])
    )
    tolerances = []

    def goto(_client, _x, _y, _z, **kwargs):
        tolerances.append(kwargs["tolerance"])
        return False

    assert storage_safety.load_storage_chunk(client, (0, 64, 0), goto)
    assert tolerances == [storage_safety.STORAGE_CHUNK_LOAD_RADIUS]
    assert not hasattr(client, "_unreachable_storage_until")


def test_chunk_loader_still_rejects_a_stalled_out_of_range_route():
    client = SimpleNamespace(
        transport=_StateTransport([(8.0, 64.0, 0.0), (4.6, 64.0, 0.0)])
    )

    assert not storage_safety.load_storage_chunk(
        client,
        (0, 64, 0),
        lambda *_args, **_kwargs: False,
    )
    assert (0, 64, 0) in client._unreachable_storage_until
