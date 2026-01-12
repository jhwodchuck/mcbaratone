"""
Checkpoint Executor Module

Provides checkpoint recovery for multi-step craft plans, allowing
plans to resume from the last successful step after failure.
"""

import time
from typing import Dict, List, Optional, Tuple, Callable


class CraftPlanExecutor:
    """
    Execute craft plans with checkpoint recovery.
    
    Tracks progress in suite_state to allow resuming from the last successful step
    if the plan is interrupted or fails partway through.
    
    Example:
        >>> plan = [
        ...     ("craft", "minecraft:oak_planks", 64),
        ...     ("smelt", "minecraft:cobblestone", "minecraft:stone", 32),
        ...     ("craft", "minecraft:stone_bricks", 32),
        ... ]
        >>> executor = CraftPlanExecutor(ctx, suite_state, "t1002b_craft")
        >>> success = executor.execute(plan, craft_fn, smelt_fn)
    """
    
    def __init__(self, ctx, suite_state: Dict, checkpoint_key: str):
        """
        Initialize executor with checkpoint tracking.
        
        Args:
            ctx: Test context
            suite_state: Suite state dict for persistence
            checkpoint_key: Unique key for this plan's checkpoint state
        """
        self.ctx = ctx
        self.suite_state = suite_state
        self.checkpoint_key = checkpoint_key
        self._completed_key = f"{checkpoint_key}_completed"
        self._failed_key = f"{checkpoint_key}_failed_at"
    
    @property
    def completed_steps(self) -> int:
        """Number of successfully completed steps."""
        return self.suite_state.get(self._completed_key, 0)
    
    @property
    def failed_step(self) -> Optional[int]:
        """Index of failed step, or None if no failure recorded."""
        return self.suite_state.get(self._failed_key)
    
    def reset(self) -> None:
        """Reset checkpoint state to start from beginning."""
        self.suite_state[self._completed_key] = 0
        self.suite_state.pop(self._failed_key, None)
    
    def execute(
        self,
        plan: List[tuple],
        craft_fn: Callable[[str, int], bool],
        smelt_fn: Optional[Callable[[str, str, int], bool]] = None,
        command_fn: Optional[Callable[[str], None]] = None,
        continue_on_failure: bool = False,
    ) -> bool:
        """
        Execute a craft plan with checkpoint recovery.
        
        Args:
            plan: List of step tuples:
                - ("craft", item_id, count)
                - ("smelt", input_id, output_id, count)
                - ("command", command_string)
                - ("log", message)
                - ("ensure_table",)
            craft_fn: Function for craft steps: (item_id, count) -> bool
            smelt_fn: Optional function for smelt steps: (input_id, output_id, count) -> bool
            command_fn: Optional function for command steps: (command) -> None
                        Falls back to ctx.run_command if not provided
            continue_on_failure: If True, continue executing after a step fails
            
        Returns:
            True if all steps completed successfully (no failures recorded)
        """
        start_step = self.completed_steps
        total_steps = len(plan)
        
        if start_step > 0:
            self.ctx.log_event(
                f"Resuming {self.checkpoint_key} from step {start_step}/{total_steps}"
            )
        
        for i, step in enumerate(plan[start_step:], start=start_step):
            op = step[0]
            step_ok = True
            
            try:
                if op == "craft":
                    _, item_id, count = step
                    result = craft_fn(item_id, count)
                    if result is False:
                        self.ctx.log_event(f"Craft failed at step {i+1}: {item_id} x{count}")
                        step_ok = False
                        
                elif op == "smelt":
                    if smelt_fn is None:
                        self.ctx.log_event(f"No smelt function provided at step {i+1}")
                        step_ok = False
                    else:
                        _, input_id, output_id, count = step
                        result = smelt_fn(input_id, output_id, count)
                        if result is False:
                            self.ctx.log_event(f"Smelt failed at step {i+1}: {input_id}->{output_id}")
                            step_ok = False
                            
                elif op == "command":
                    _, command = step
                    if command_fn:
                        command_fn(command)
                    else:
                        self.ctx.run_command(command)
                        
                elif op == "log":
                    _, message = step
                    self.ctx.log_event(message)
                    
                elif op == "ensure_table":
                    # Caller should handle this in their craft_fn if needed
                    pass
                    
                else:
                    self.ctx.log_event(f"Unknown operation '{op}' at step {i+1}")
                    # Don't fail on unknown ops - may be caller-specific
                    
            except Exception as e:
                self.ctx.log_event(f"Exception at step {i+1}: {type(e).__name__}: {e}")
                step_ok = False
            
            if step_ok:
                # Checkpoint successful step
                self.suite_state[self._completed_key] = i + 1
            else:
                # Record failure point
                self.suite_state[self._failed_key] = i
                if not continue_on_failure:
                    self.ctx.log_event(f"Stopping at step {i+1}/{total_steps} due to failure")
                    return False
        
        # Plan completed
        self.ctx.log_event(f"Craft plan {self.checkpoint_key}: {total_steps} steps complete")
        return self.failed_step is None


def get_checkpoint_progress(suite_state: Dict, checkpoint_key: str) -> Dict:
    """
    Get checkpoint progress for a craft plan.
    
    Args:
        suite_state: Suite state dict
        checkpoint_key: The checkpoint key used by CraftPlanExecutor
        
    Returns:
        Dict with:
            - 'completed': Number of completed steps
            - 'failed_at': Index of failed step (or None)
            - 'can_resume': True if plan can be resumed from checkpoint
    """
    completed = suite_state.get(f"{checkpoint_key}_completed", 0)
    failed_at = suite_state.get(f"{checkpoint_key}_failed_at")
    
    return {
        "completed": completed,
        "failed_at": failed_at,
        "can_resume": completed > 0 and failed_at is not None,
    }


__all__ = ["CraftPlanExecutor", "get_checkpoint_progress"]
