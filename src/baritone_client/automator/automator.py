"""
EndGame Automator - Main orchestrator for end-game automation.
"""

import time
from typing import Optional, Callable, Any
from .state_manager import StateManager, Phase
from .resource_manager import ResourceManager
from .phase_executor import PhaseExecutor, PhaseHandler
from .coordination_hub import CoordinationHub, SystemEvent, EventType
from .systems import SafetySystem, HungerSystem, MappingSystem
from .telemetry import TelemetrySystem


class EndGameAutomator:
    """
    Main orchestrator for end-game automation.
    
    Coordinates:
    - State management (phases, checkpoints)
    - Resource tracking (inventory, requirements)
    - Phase execution (handlers, retries)
    
    Example:
        ```python
        from baritone_client import Client, TcpTransport
        from baritone_client.automator import EndGameAutomator
        
        transport = TcpTransport()
        client = Client(transport)
        
        automator = EndGameAutomator(client)
        automator.register_default_handlers()
        automator.run()
        ```
    """
    
    def __init__(
        self,
        client,
        checkpoint_dir: Optional[str] = None,
        auto_checkpoint: bool = True,
        checkpoint_interval: float = 60.0,
    ):
        """
        Initialize the automator.
        
        Args:
            client: Baritone client instance
            checkpoint_dir: Directory for checkpoint files
            auto_checkpoint: Whether to auto-save checkpoints
            checkpoint_interval: Seconds between auto-checkpoints
        """
        self.client = client
        self.state = StateManager(checkpoint_dir)
        self.resources = ResourceManager(client)
        self.coordination = CoordinationHub()
        self.systems = [
            SafetySystem(client, self.coordination, resources=self.resources),
            HungerSystem(client, self.coordination, resources=self.resources),
            MappingSystem(client, self.coordination, resources=self.resources)
        ]
        self.executor = PhaseExecutor(client, self.resources, self.state, self.coordination)
        self.telemetry = TelemetrySystem(checkpoint_dir)
        
        self.auto_checkpoint = auto_checkpoint
        self.checkpoint_interval = checkpoint_interval
        self._last_checkpoint = 0.0
        self._running = False
        
        # Callbacks
        self.on_phase_start: Optional[Callable[[Phase], None]] = None
        self.on_phase_complete: Optional[Callable[[Phase], None]] = None
        self.on_phase_fail: Optional[Callable[[Phase], None]] = None
        self.on_complete: Optional[Callable[[], None]] = None
    
    def register_handler(self, phase: Phase, handler: PhaseHandler) -> None:
        """Register a phase handler."""
        self.executor.register_handler(phase, handler)
    
    def register_default_handlers(self) -> None:
        """Register all default phase handlers."""
        from .phases import (
            SpawnBootstrapHandler,
            InitialGatheringHandler,
            BaseConstructionHandler,
            IronAgeHandler,
            DiamondMiningHandler,
            EnchantingHandler,
            NetherPrepHandler,
            NetherTravelHandler,
            EnderPearlHandler,
            StrongholdHandler,
            EndPortalHandler,
            DragonFightHandler,
            BridgeCheckHandler,
        )
        
        self.register_handler(Phase.SPAWN_BOOTSTRAP, SpawnBootstrapHandler())
        self.register_handler(Phase.INITIAL_GATHERING, InitialGatheringHandler())
        self.register_handler(Phase.BASE_CONSTRUCTION, BaseConstructionHandler())
        self.register_handler(Phase.IRON_AGE, IronAgeHandler())
        self.register_handler(Phase.DIAMOND_MINING, DiamondMiningHandler())
        self.register_handler(Phase.ENCHANTING, EnchantingHandler())
        self.register_handler(Phase.NETHER_PREP, NetherPrepHandler())
        self.register_handler(Phase.NETHER_TRAVEL, NetherTravelHandler())
        self.register_handler(Phase.ENDER_PEARL_FARM, EnderPearlHandler())
        self.register_handler(Phase.STRONGHOLD_LOCATE, StrongholdHandler())
        self.register_handler(Phase.END_PORTAL, EndPortalHandler())
        self.register_handler(Phase.DRAGON_FIGHT, DragonFightHandler())
        self.register_handler(Phase.BRIDGE_CHECK, BridgeCheckHandler())
        
    def _get_current_seed(self) -> Optional[int]:
        """Fetch current world seed from bridge."""
        try:
            state = self.client.transport.dispatch("get_state", {})
            return state.get("world_seed")
        except Exception:
            return None
    
    def load_or_start(self) -> Phase:
        """
        Load checkpoint or start fresh.
        
        Returns:
            Starting phase
        """
        seed = self._get_current_seed()
        if self.state.load_checkpoint(current_seed=seed):
            print(f"Resumed from checkpoint: {self.state.get_current_phase().name}")
            if seed:
                print(f"  World seed: {seed}")
        else:
            print("Starting fresh automation")
        
        return self.state.get_current_phase()
    
    def run(self, resume: bool = True) -> bool:
        """
        Run the automation loop.
        
        Args:
            resume: Whether to attempt checkpoint resumption
            
        Returns:
            True if dragon defeated, False if stopped/failed
        """
        self._running = True
        
        if resume:
            self.load_or_start()
            
        # Initialize dynamic resources
        self.resources.initialize_recipes()
        
        # Start background systems
        for system in self.systems:
            system.start()
        
        print(f"\n{'#'*60}")
        print(f"#  EndGame Automator Started")
        print(f"#  Current Phase: {self.state.get_current_phase().name}")
        print(f"#  Overall Progress: {self.state.get_overall_progress()*100:.1f}%")
        print(f"{'#'*60}\n")
        
        while self._running and self.state.get_current_phase() != Phase.COMPLETE:
            phase = self.state.get_current_phase()

            # Check for death and handle recovery
            if self._handle_death_recovery():
                continue  # Skip to next phase after recovery

            # Notify phase start
            if self.on_phase_start:
                self.on_phase_start(phase)
            
            # Start timing
            self.telemetry.start_timer(f"phase_{phase.name}")
            
            # Execute phase
            success = self.executor.execute_phase(phase)
            
            # Stop timing
            self.telemetry.stop_timer(f"phase_{phase.name}", success=success)
            
            if success:
                if self.on_phase_complete:
                    self.on_phase_complete(phase)
                
                # Advance to next phase
                payload = self.state.get_phase_payload(phase)
                if payload:
                    print(f"Phase {phase.name} data: {payload}")
                self.state.advance_phase()
                
                # Auto checkpoint
                self._maybe_checkpoint()
                
            else:
                if self.on_phase_fail:
                    self.on_phase_fail(phase)
                
                print(f"\nPhase {phase.name} failed. Stopping automation.")
                self._running = False
                return False
        
        if self.state.get_current_phase() == Phase.COMPLETE:
            print("\n" + "="*60)
            print("  🐉 ENDER DRAGON DEFEATED! 🎉")
            print("  EndGame Automation Complete!")
            print("="*60 + "\n")
            
            if self.on_complete:
                self.on_complete()
            
            self.state.clear_checkpoint()
            return True
        
        return False
    
    def stop(self) -> None:
        """Stop the automation loop."""
        self._running = False
        for system in self.systems:
            system.stop()
        self._save_checkpoint()
        self.telemetry.write_report()
        self.telemetry.write_log()
    
    def _maybe_checkpoint(self) -> None:
        """Save checkpoint if interval elapsed."""
        if not self.auto_checkpoint:
            return
        
        now = time.time()
        if now - self._last_checkpoint >= self.checkpoint_interval:
            self._save_checkpoint()
            self._last_checkpoint = now
    
    def _save_checkpoint(self) -> None:
        """Save current state to checkpoint."""
        self.resources.refresh_inventory()
        inventory = self.resources.cached_inventory
        seed = self._get_current_seed()
        path = self.state.save_checkpoint(inventory, world_seed=seed)
        print(f"Checkpoint saved: {path}")
    
    def _handle_death_recovery(self) -> bool:
        """Handle player death and recovery. Returns True if recovery was needed."""
        try:
            state = self.client.transport.dispatch("get_state", {})
            if not state.get("is_dead", False) and state.get("health", 20) > 0:
                return False  # No death to handle

            print("\n!!! PLAYER DIED !!!")
            print("Starting recovery sequence...")

            # Respawn
            self.client.transport.dispatch("respawn", {})
            time.sleep(2.0)

            # Get death location and recover items
            response = self.client.transport.dispatch("get_death_location", {})
            if response.get("status") == "ok":
                data = response.get("data", {})
                x, y, z = data.get("x"), data.get("y"), data.get("z")
                dim = data.get("dimension")

                if x is not None:
                    print(f"Death location: ({x}, {y}, {z}) in {dim}")
                    # Navigate to death location to recover items
                    from ..common import goto
                    success = goto(self.client, int(x), int(y), int(z), timeout=600)
                    if success:
                        print("Recovered items from death location")
                        time.sleep(2.0)  # Wait for item pickup
                    else:
                        print("Failed to reach death location")

            # Reset to bootstrap phase for fresh start
            self.state.set_phase(Phase.SPAWN_BOOTSTRAP)
            print("Reset to SPAWN_BOOTSTRAP phase")
            return True

        except Exception as e:
            print(f"Warning: Failed to handle death recovery: {e}")
            return False

    def get_status(self) -> dict:
        """Get current automation status."""
        return {
            "running": self._running,
            "current_phase": self.state.get_current_phase().name,
            "phase_index": self.state.get_phase_index(),
            "total_phases": self.state.get_total_phases(),
            "phase_progress": self.state.get_progress(),
            "overall_progress": self.state.get_overall_progress(),
            "resources": self.resources.get_summary(),
        }
