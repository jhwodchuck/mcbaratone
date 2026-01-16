"""
Structured Logging Module for Farming Suites.

Provides consistent, timestamped, and categorized logging through bridging.
Uses context.log_event() exclusively to avoid double-logging with stdout.
"""

class FarmingLogger:
    def __init__(self, ctx, run_id: int):
        self.ctx = ctx
        self.header = f"[Run {run_id}]"

    def _log(self, category: str, message: str):
        """
        Central logging point.
        Format: [Run X] [CATEGORY] Message
        """
        line = f"{self.header} [{category}] {message}"
        self.ctx.log_event(line)

    def info(self, message: str):
        """General info log."""
        self._log("INFO", message)

    def warn(self, message: str):
        """Warning log."""
        self._log("WARN", message)

    def error(self, message: str):
        """Error log."""
        self._log("ERROR", message)

    def mining_update(self, logs: int, gain: int, pos: tuple, pathing: str = "False"):
        """Structured mining progress update."""
        self._log("MINING", f"Logs: {logs} (+{gain}) | Pos: {pos} | Pathing: {pathing}")

    def status(self, health, hunger, inventory_summary, time_info: str = ""):
        """Detailed player status log."""
        # Format matches user preference: HP=X Hunger=X | Pos | Time | Inv
        # Note: inventory_summary is expected to be a string already formatted or dict
        if isinstance(inventory_summary, dict):
             inv_str = ", ".join([f"{k.replace('minecraft:', '')}: {v}" for k, v in inventory_summary.items()]) if inventory_summary else "empty"
        else:
            inv_str = str(inventory_summary)
            
        self._log("STATUS", f"HP={health} Food={hunger}{time_info} | Inv: {inv_str}")
