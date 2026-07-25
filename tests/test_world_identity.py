import gzip
import struct
from types import SimpleNamespace

from baritone_client.automator.automator import EndGameAutomator
from baritone_client.world_identity import read_level_dat_seed


def _name(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack(">H", len(encoded)) + encoded


def test_read_level_dat_seed_reads_worldgen_settings(tmp_path):
    expected = -782345678901234567
    payload = b"".join(
        (
            b"\x0a" + _name(""),
            b"\x0a" + _name("Data"),
            b"\x0a" + _name("WorldGenSettings"),
            b"\x04" + _name("seed") + struct.pack(">q", expected),
            b"\x00",  # WorldGenSettings
            b"\x00",  # Data
            b"\x00",  # root
        )
    )
    level_dat = tmp_path / "level.dat"
    with gzip.open(level_dat, "wb") as handle:
        handle.write(payload)

    assert read_level_dat_seed(level_dat) == expected


def test_world_seed_override_strengthens_dedicated_server_identity(tmp_path):
    class Transport:
        host = "localhost"
        port = 5610

        def dispatch(self, route, _payload):
            if route == "get_state":
                return {"dimension": "minecraft:overworld"}
            return {}

    client = SimpleNamespace(transport=Transport())
    automator = EndGameAutomator(
        client,
        checkpoint_dir=tmp_path,
        world_seed_override=123456789,
    )

    identity = automator._get_current_world_identity()
    assert identity.seed == 123456789
    assert identity.server_address == "localhost:5610"
    assert identity.strength == "strong"

