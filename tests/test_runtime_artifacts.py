from types import SimpleNamespace

from baritone_client.common.runtime_artifacts import (
    append_world_map_entry,
    runtime_artifact_path,
)


def test_runtime_world_map_is_bot_local_and_deduplicated(tmp_path):
    state = SimpleNamespace(checkpoint_dir=tmp_path / "Bot03")
    position = (-433, 73, 301)

    assert append_world_map_entry(
        "Crafting Table/Base",
        position,
        state=state,
    )
    assert not append_world_map_entry(
        "Crafting Table/Base",
        position,
        state=state,
    )

    path = runtime_artifact_path("world_map.md", state)
    assert path == tmp_path / "Bot03" / "monitor" / "world_map.md"
    assert path.read_text(encoding="utf-8").count(str(position)) == 1
