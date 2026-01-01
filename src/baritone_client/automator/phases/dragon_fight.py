"""
Dragon Fight Phase - Defeat the Ender Dragon.
"""

import time
from ..phase_executor import PhaseHandler
from ..resource_manager import ResourceManager
from ..state_manager import Phase, StateManager
from ...common import goto, find_nearby_block, select_item, heal_if_needed
from ...common.tasks import TaskResult


class DragonFightHandler(PhaseHandler):
    """Handler for dragon fight phase."""
    
    def get_name(self) -> str:
        return "Ender Dragon Fight"
    
    def execute(self, client, resources: ResourceManager, state: StateManager) -> TaskResult:
        """Fight and defeat the Ender Dragon with detailed logic."""
        print("Initiating final battle with the Ender Dragon...")
        ready = resources.phase_ready_result(Phase.DRAGON_FIGHT, "Dragon fight requirements already satisfied")
        if ready:
            return ready

        # Ensure we are in The End
        snapshot = client.transport.dispatch("get_state", {})
        dimension = snapshot.get("dimension", snapshot.get("world", {}).get("dimension", ""))
        if "the_end" not in dimension.lower():
            return TaskResult.fail("Must be in The End to fight the dragon.")

        # Start the dragon fight logic
        if self._fight_dragon(client):
            resources.refresh_inventory()
            missing = resources.check_phase_requirements(Phase.DRAGON_FIGHT)
            return TaskResult.ok(
                "Ender Dragon defeated",
                missing=missing,
                inventory=resources.get_summary()["inventory"],
            )
        
        return TaskResult.fail("Dragon fight failed")

    def _fight_dragon(self, client, timeout: int = 1200) -> bool:
        """Detailed dragon fight logic: crystal destruction, perch detection, breath avoidance, victory detection."""
        print("Beginning Ender Dragon fight sequence...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # Victory detection: Check if dragon is dead
            status = client.mission.status()
            if status.get("mission", {}).get("note") == "dragon_defeated":
                return True
            
            # Breath avoidance: Check for dragon breath (area effect cloud)
            entities = client.transport.dispatch("get_entities", {"radius": 32})
            breath_clouds = [e for e in entities.get("entities", []) if e.get("type") == "minecraft:area_effect_cloud"]
            if breath_clouds:
                print("Dragon breath detected! Moving away...")
                player_pos = client.transport.dispatch("get_state", {}).get("position", {})
                px, py, pz = player_pos.get("x", 0), player_pos.get("y", 64), player_pos.get("z", 0)
                # Move to a safe position, e.g., away from clouds
                safe_x, safe_z = 0, 0  # Towards center or away
                for cloud in breath_clouds:
                    cx, cz = cloud.get("position", {}).get("x", 0), cloud.get("position", {}).get("z", 0)
                    dx, dz = px - cx, pz - cz
                    dist = (dx**2 + dz**2)**0.5
                    if dist < 5:  # Too close
                        safe_x += dx / dist * 10  # Move 10 blocks away
                        safe_z += dz / dist * 10
                goto(client, int(px + safe_x), int(py), int(pz + safe_z), tolerance=5)
                continue
            
            # Heal if needed
            heal_if_needed(client)
            
            # Crystal destruction
            crystal_pos = find_nearby_block(client, ["minecraft:end_crystal"], radius=128)
            if crystal_pos:
                print(f"Targeting crystal at {crystal_pos}")
                # Climb if high up (perch detection for crystals)
                if crystal_pos[1] > 70:
                    print("Climbing pillar to reach crystal...")
                    goto(client, crystal_pos[0], crystal_pos[1], crystal_pos[2], tolerance=10)
                
                # Attack crystal with bow or melee
                if select_item(client, "minecraft:bow") or select_item(client, "minecraft:snowball"):
                    client.transport.dispatch("look_at", {"x": crystal_pos[0], "y": crystal_pos[1], "z": crystal_pos[2]})
                    client.transport.dispatch("use_item", {})
                else:
                    # Melee attack (dangerous)
                    goto(client, *crystal_pos, tolerance=3)
                    # Assuming attack_entity can target blocks or entities nearby
                    client.transport.dispatch("attack_entity", {"id": "minecraft:end_crystal"})
                time.sleep(1)
                continue
            
            # Attack Dragon
            dragon = next((e for e in entities.get("entities", []) if e.get("type") == "minecraft:ender_dragon"), None)
            if dragon:
                dx, dy, dz = dragon["position"]["x"], dragon["position"]["y"], dragon["position"]["z"]
                print(f"Dragon spotted at ({dx}, {dy}, {dz})")
                
                # Perch detection: If dragon is low and near center
                if abs(dx) < 10 and abs(dz) < 10 and dy < 80:
                    print("Dragon is perched! Melee attack!")
                    goto(client, 0, 64, 0, tolerance=2)
                    client.transport.dispatch("attack_entity", {"type": "minecraft:ender_dragon"})
                else:
                    # Snipe with bow
                    if select_item(client, "minecraft:bow"):
                        client.transport.dispatch("look_at", {"x": dx, "y": dy, "z": dz})
                        client.transport.dispatch("use_item", {})
            else:
                # If no dragon, perhaps wait or explore
                pass
            
            time.sleep(1)
        
        return False
