"""Bounded, checkpointed terraforming after megabase initialization."""

from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import TaskResult
from ...common.terraform import CHUNK_SIZE, terraform_area


def _position(value):
    try:
        if isinstance(value, dict) and all(axis in value for axis in ("x", "z")):
            return int(value["x"]), int(value["z"])
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return int(value[0]), int(value[2])
    except (TypeError, ValueError):
        return None
    return None


def _preserved_chunk_origins(state: StateManager, names) -> tuple[set, list[str]]:
    """Resolve the megabase handoff's named preserves into whole chunks."""
    structures = state.custom_data.get("structures", {})
    locations = state.custom_data.get("locations", {})
    resolved = {}

    beacon = structures.get("beacon", {}) if isinstance(structures, dict) else {}
    resolved["beacon"] = [_position(beacon.get("location"))]
    for category in ("end_portal", "nether_portal"):
        entries = locations.get(category, []) if isinstance(locations, dict) else []
        resolved[category] = [
            _position(entry)
            for entry in entries
            if isinstance(entry, dict)
            and "nether" not in str(entry.get("dimension", "overworld")).lower()
        ]

    storage = []
    if isinstance(structures, dict):
        for key in ("starter_house", "bootstrap_base"):
            record = structures.get(key, {})
            if isinstance(record, dict):
                storage.append(_position(record.get("supply_chest")))
    if isinstance(locations, dict):
        for category in ("storage", "chest", "supply_chest"):
            storage.extend(_position(entry) for entry in locations.get(category, []))
    resolved["storage"] = storage

    chunks = set()
    missing = []
    for name in names:
        positions = [value for value in resolved.get(str(name), []) if value]
        if not positions:
            missing.append(str(name))
            continue
        chunks.update(
            ((x // CHUNK_SIZE) * CHUNK_SIZE, (z // CHUNK_SIZE) * CHUNK_SIZE)
            for x, z in positions
        )
    return chunks, missing


class TerraformingHandler(PhaseHandler):
    """Flattens a bounded area around the player/base to a target height."""

    def __init__(self, radius_chunks: int = 12, fill_block: str = "minecraft:stone"):
        self.radius_chunks = radius_chunks
        self.fill_block = fill_block

    def get_name(self) -> str:
        return "Terraforming"

    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        plan = state.custom_data.get("terraform_plan")
        if not isinstance(plan, dict):
            return TaskResult.fail("Megabase terraform plan is missing")
        center = plan.get("center")
        if not isinstance(center, (list, tuple)) or len(center) != 2:
            return TaskResult.fail("Terraform plan center is invalid")
        try:
            center_x, center_z = (int(value) for value in center)
            target_y = int(plan["target_y"])
            radius_chunks = int(plan.get("radius_chunks", self.radius_chunks))
        except (KeyError, TypeError, ValueError) as exc:
            return TaskResult.fail(f"Terraform plan is invalid: {exc}")
        if radius_chunks < 0:
            return TaskResult.fail("Terraform radius must be non-negative")
        preserve_names = plan.get("preserve", [])
        if not isinstance(preserve_names, list):
            return TaskResult.fail("Terraform preserve list is invalid")
        protected_chunks, missing_preserves = _preserved_chunk_origins(
            state, preserve_names
        )
        if missing_preserves:
            return TaskResult.fail(
                "Terraform preserve locations are unresolved",
                missing_preserves=missing_preserves,
            )

        progress = state.custom_data.setdefault("terraform_progress", {})

        def on_chunk_done(done, total, chunk_coord):
            print(f"  [Terraforming] chunk {done}/{total} -> {chunk_coord}")

        result = terraform_area(
            client,
            center_x, center_z, target_y,
            radius_chunks=radius_chunks,
            fill_block=self.fill_block,
            progress=progress,
            on_chunk_done=on_chunk_done,
            skip_chunks=protected_chunks,
        )
        if not result.success:
            return result

        chunks_total = int(result.data.get("chunks_total", 0) or 0)
        chunks_completed = int(result.data.get("chunks_completed", 0) or 0)
        progress_entries_total = int(
            result.data.get("progress_entries_total", 0) or 0
        )
        if (
            chunks_total <= 0
            or chunks_completed != chunks_total
            or int(progress.get("next_index", 0) or 0) != progress_entries_total
        ):
            return TaskResult.fail(
                "Terraform operation did not reach its persisted boundary",
                chunks_completed=chunks_completed,
                chunks_total=chunks_total,
            )
        payload = {
            "verified_operations": True,
            "progress_complete": True,
            "chunks_completed": chunks_completed,
            "chunks_total": chunks_total,
            "progress_entries_total": progress_entries_total,
            "skipped_chunks": result.data.get("skipped_chunks", []),
            "center": [center_x, center_z],
            "target_y": target_y,
            "radius_chunks": radius_chunks,
            "fill_block": self.fill_block,
            "terraform_plan": dict(plan),
        }
        state.record_phase_payload(Phase.TERRAFORM, payload)
        return TaskResult.ok("Bounded terraform plan completed", **payload)
