"""Clearing a wrong house block is mining, and mining needs a pickaxe.

Live A1 spent hours looping every ~70s on a single `cobbled_deepslate` floor
tile at (-412, 78, -15): its pickaxe had worn out, a bare hand cannot clear
deepslate inside the 12s break deadline, so the tile "remained", the repair
reported failed=1, and ENCHANTING_PIPELINE stayed hard-gated behind
"Starter-house integrity failed". Every other mining entry point
(gather_stone, gather_ores, stone_descent) guards itself with
`_ensure_mining_pickaxe`; this one did not.
"""

from __future__ import annotations

from types import SimpleNamespace

from baritone_client.common import base


def _client(block_sequence):
    """A client whose target block reports each value in turn."""
    seen = {"dispatched": []}
    blocks = list(block_sequence)

    def dispatch(route, payload=None):
        seen["dispatched"].append(route)
        if route == "get_block":
            return {"id": blocks.pop(0) if len(blocks) > 1 else blocks[0]}
        return {}

    return SimpleNamespace(transport=SimpleNamespace(dispatch=dispatch)), seen


def test_clearing_a_wrong_target_ensures_a_pickaxe_first(monkeypatch):
    ensured = []
    monkeypatch.setattr(
        "baritone_client.common.resources._ensure_mining_pickaxe",
        lambda client: ensured.append(client) or True,
    )
    client, seen = _client(["minecraft:cobbled_deepslate", "minecraft:air"])

    assert base._clear_wrong_house_target(
        client, -412, 78, -15, "minecraft:cobblestone"
    )
    assert ensured, "a break with no pickaxe guarantee is the A1 deepslate loop"
    # The tool guarantee must precede the break, not follow it.
    assert seen["dispatched"].index("break_block") > 0


def test_an_already_correct_target_does_not_go_looking_for_a_pickaxe(monkeypatch):
    """Repair must stay cheap when there is nothing to clear."""
    ensured = []
    monkeypatch.setattr(
        "baritone_client.common.resources._ensure_mining_pickaxe",
        lambda client: ensured.append(client) or True,
    )
    client, seen = _client(["minecraft:cobblestone"])

    assert base._clear_wrong_house_target(
        client, -412, 78, -15, "minecraft:cobblestone"
    )
    assert not ensured
    assert "break_block" not in seen["dispatched"]
