from typing import Any, Dict, Optional

from ...transport.command_dispatcher import CommandDispatcher
from ...core.exceptions import CommandError, ValidationError
from ...transport.transport import Transport


class CommandFacade:
    """Facade for executing Baritone commands using CommandDispatcher."""

    def __init__(self, transport: Transport) -> None:
        self.dispatcher = CommandDispatcher(transport)

    def run(self, command: str) -> Dict[str, Any]:
        """
        Execute a Baritone command string.

        Args:
            command: Command string (e.g., "#goto 100 64 200" or "goto 100 64 200")

        Returns:
            Response dictionary from the bridge

        Raises:
            ValidationError: If command is empty
            CommandError: If command execution fails
            TransportError: If transport fails
        """
        if not command or not command.strip():
            raise ValidationError("Command cannot be empty", field="command")
        result = self.dispatcher.dispatch("chat", {"message": command})
        if result.is_success():
            return result.get_data()
        else:
            raise CommandError(result.get_error_message() or "Command execution failed")
    
    def explore(self, x: int = 0, z: int = 0) -> Dict[str, Any]:
        """
        Start exploration process.

        Args:
            x: Starting X coordinate (default: 0)
            z: Starting Z coordinate (default: 0)

        Returns:
            Response dictionary
        """
        result = self.dispatcher.dispatch("explore", {"x": x, "z": z})
        if result.is_success():
            return result.get_data()
        else:
            raise CommandError(result.get_error_message() or "Explore command failed")
    
    def follow(self, entity: str = "player") -> Dict[str, Any]:
        """
        Follow an entity.

        Args:
            entity: Entity name or "player" (default: "player")

        Returns:
            Response dictionary
        """
        result = self.dispatcher.dispatch("follow", {"entity": entity})
        if result.is_success():
            return result.get_data()
        else:
            raise CommandError(result.get_error_message() or "Follow command failed")
    
    def cancel(self) -> Dict[str, Any]:
        """
        Cancel current operation.

        Returns:
            Response dictionary
        """
        result = self.dispatcher.dispatch("cancel", {})
        if result.is_success():
            return result.get_data()
        else:
            raise CommandError(result.get_error_message() or "Cancel command failed")
    
    def get_block(self, x: int, y: int, z: int) -> Dict[str, Any]:
        """
        Get block information at coordinates.

        Args:
            x: X coordinate
            y: Y coordinate
            z: Z coordinate

        Returns:
            Block information dictionary
        """
        result = self.dispatcher.dispatch("get_block", {"x": x, "y": y, "z": z})
        if result.is_success():
            return result.get_data()
        else:
            raise CommandError(result.get_error_message() or "Get block command failed")

    def craft(self, recipe_id: str, count: int = 1) -> Dict[str, Any]:
        """
        Craft an item using a bridge-supported recipe.

        Args:
            recipe_id: Identifier for the recipe on the bridge side.
            count: Number of times to craft.

        Returns:
            Response data from the bridge.
        """
        payload = {"recipe_id": recipe_id, "count": count}
        result = self.dispatcher.dispatch("craft", payload)
        if result.is_success():
            return result.get_data()
        else:
            raise CommandError(result.get_error_message() or "Craft command failed")

    def smelt(self, input_item: str, count: int = 1, fuel_item: str = "minecraft:coal") -> Dict[str, Any]:
        """
        Smelt items in a furnace (bridge stub).

        Args:
            input_item: Item id to smelt.
            count: Number of items to smelt.
            fuel_item: Fuel item id (defaults to coal).
        """
        payload = {"input_item": input_item, "count": count, "fuel_item": fuel_item}
        result = self.dispatcher.dispatch("smelt", payload)
        if result.is_success():
            return result.get_data()
        else:
            raise CommandError(result.get_error_message() or "Smelt command failed")

    def goto(self, x: Optional[int] = None, y: Optional[int] = None, z: Optional[int] = None, target: Optional[str] = None) -> Dict[str, Any]:
        """
        Go to coordinates or a target.
        
        Args:
            x, y, z: Coordinates
            target: Target name (e.g. "portal", "ender_chest", "death") or block type
        """
        if target:
            return self.run(f"#goto {target}")
        if x is not None and z is not None:
            cmd = f"#goto {x} {z}"
            if y is not None:
                cmd = f"#goto {x} {y} {z}"
            return self.run(cmd)
        raise ValidationError("Must provide coordinates or target for goto")

    def thisway(self, distance: int) -> Dict[str, Any]:
        """Go in the direction you're facing."""
        return self.run(f"#thisway {distance}")

    def come(self) -> Dict[str, Any]:
        """Tell Baritone to head towards your camera."""
        return self.run("#come")

    def invert(self) -> Dict[str, Any]:
        """Invert the current goal and path (run away)."""
        return self.run("#invert")

    def surface(self) -> Dict[str, Any]:
        """Head towards the closest surface-like area."""
        return self.run("#surface")

    def axis(self) -> Dict[str, Any]:
        """Go to an axis or diagonal axis."""
        return self.run("#axis")

    def tunnel(self, height: int = 2, width: int = 1, depth: int = 100) -> Dict[str, Any]:
        """
        Dig a tunnel.
        Args:
             height: Tunnel height (default 2)
             width: Tunnel width (default 1)
             depth: Tunnel depth/length (default 100)
        """
        return self.run(f"#tunnel {height} {width} {depth}")

    def find(self, block: str) -> Dict[str, Any]:
        """Search cache for a block."""
        return self.run(f"#find {block}")

    def click(self, mode: str = "path") -> Dict[str, Any]:
        """Click destination. mode can be 'path' (right click) or 'force' (left click)."""
        # Simplification of complex click logic. Just running basic #click
        return self.run("#click")

    def goal(self, x: int, y: Optional[int] = None, z: Optional[int] = None) -> Dict[str, Any]:
        """Set a goal coordinate (does not start pathing)."""
        if y is None and z is None:
             # goal y
             return self.run(f"#goal {x}") 
        if z is None:
             # goal x z
             return self.run(f"#goal {x} {y}")
        return self.run(f"#goal {x} {y} {z}")

    def goal_clear(self) -> Dict[str, Any]:
        """Clear the goal."""
        return self.run("#goal clear")

    def wp_save(self, name: str) -> Dict[str, Any]:
        """Save a waypoint."""
        return self.run(f"#wp save {name}")

    def wp_goal(self, name: str) -> Dict[str, Any]:
        """Set goal to a waypoint."""
        return self.run(f"#wp goal {name}")

    def wp_list(self, tag: str = "user") -> Dict[str, Any]:
        """List waypoints."""
        return self.run(f"#wp list {tag}")

    def eta(self) -> Dict[str, Any]:
        """Get ETA information."""
        return self.run("#eta")

    def proc(self) -> Dict[str, Any]:
        """View process information."""
        return self.run("#proc")

    def repack(self) -> Dict[str, Any]:
        """Re-cache chunks."""
        return self.run("#repack")

    def gc(self) -> Dict[str, Any]:
        """Run System.gc()."""
        return self.run("#gc")

    def render(self) -> Dict[str, Any]:
        """Fix glitched chunk rendering."""
        return self.run("#render")

    def reloadall(self) -> Dict[str, Any]:
        """Reload Baritone's world cache."""
        return self.run("#reloadall")

    def saveall(self) -> Dict[str, Any]:
        """Save Baritone's world cache."""
        return self.run("#saveall")

    def version(self) -> Dict[str, Any]:
        """Get Baritone version."""
        return self.run("#version")

    def help(self, query: Optional[str] = None) -> Dict[str, Any]:
        """Run help command."""
        cmd = "#help"
        if query:
            cmd += f" {query}"
        return self.run(cmd)

    def blacklist(self, block: Optional[str] = None) -> Dict[str, Any]:
        """
        Blacklist a block or view blacklist.
        Args:
            block: Block to blacklist (optional)
        """
        if block:
            return self.run(f"#blacklist {block}")
        return self.run("#blacklist")

    def schematica(self) -> Dict[str, Any]:
        """Build the currently open schematic in Schematica/Litematica."""
        return self.run("#schematica")

    def path(self) -> Dict[str, Any]:
        """Force path calculation to current goal."""
        return self.run("#path")

    def sel(self, command: str) -> Dict[str, Any]:
        """
        Run a selection command.
        Args:
            command: Selection subcommand (e.g. "clear", "expand 1", "set 1")
        """
        return self.run(f"#sel {command}")

    def screenshot(self, filename: Optional[str] = None, reason: str = "manual") -> Dict[str, Any]:
        """
        Capture a screenshot of the current game view.
        
        Screenshots are saved to Minecraft's screenshots/ folder.
        
        Args:
            filename: Optional custom filename (auto-generated timestamp if omitted)
            reason: Reason for screenshot (e.g., "phase_transition", "error", "manual")
            
        Returns:
            Dictionary with 'path' (file location), 'filename', 'reason', 'queued' (bool)
        """
        payload = {"reason": reason}
        if filename:
            payload["filename"] = filename
        result = self.dispatcher.dispatch("screenshot", payload)
        if result.is_success():
            return result.get_data()
        else:
            raise CommandError(result.get_error_message() or "Screenshot command failed")

