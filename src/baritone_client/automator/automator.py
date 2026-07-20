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
from ..common.nether import find_nearest_portal
from ..actions.death_recovery_action import DeathRecoveryAction
from ..core.interfaces import ActionContext, ActionResult
from ..actions.base import BaseAction
from ..actions.suites import SUITES
from ..world_identity import WorldIdentity


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
        screenshot_enabled: bool = True,
    ):
        """
        Initialize the automator.
        
        Args:
            client: Baritone client instance
            checkpoint_dir: Directory for checkpoint files
            auto_checkpoint: Whether to auto-save checkpoints
            checkpoint_interval: Seconds between auto-checkpoints
            screenshot_enabled: Capture screenshots on phase transitions
        """
        self.client = client
        self.state = StateManager(checkpoint_dir)
        self.resources = ResourceManager(client)
        self.coordination = CoordinationHub()
        self.systems = [
            SafetySystem(client, self.coordination, resources=self.resources),
            HungerSystem(client, self.coordination, resources=self.resources),
            MappingSystem(client, self.coordination, resources=self.resources, state_manager=self.state)
        ]
        self.executor = PhaseExecutor(
            client,
            self.resources,
            self.state,
            self.coordination,
            screenshot_enabled=screenshot_enabled,
        )
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
            BridgeCheckHandler,
            SpawnBootstrapHandler,
            InitialGatheringHandler,
            BaseConstructionHandler,
            BootSequenceHandler,
            FoodAndIronHandler,
            EnchantingPipelineHandler,
            NetherAndBlazeHandler,
            VillagerInfraHandler,
            XpEngineHandler,
            IronFarmHandler,
            ToolPerfectionHandler,
            WorldUnlockHandler,
            MegabaseInitHandler,
        )
        
        self.register_handler(Phase.BRIDGE_CHECK, BridgeCheckHandler())
        self.register_handler(Phase.SPAWN_BOOTSTRAP, SpawnBootstrapHandler())
        self.register_handler(Phase.INITIAL_GATHERING, InitialGatheringHandler())
        self.register_handler(Phase.BASE_CONSTRUCTION, BaseConstructionHandler())
        self.register_handler(Phase.BOOT_SEQUENCE, BootSequenceHandler())
        self.register_handler(Phase.FOOD_AND_IRON, FoodAndIronHandler())
        self.register_handler(Phase.ENCHANTING_PIPELINE, EnchantingPipelineHandler())
        self.register_handler(Phase.NETHER_AND_BLAZE, NetherAndBlazeHandler())
        self.register_handler(Phase.VILLAGER_INFRA, VillagerInfraHandler())
        self.register_handler(Phase.XP_ENGINE, XpEngineHandler())
        self.register_handler(Phase.IRON_FARM, IronFarmHandler())
        self.register_handler(Phase.TOOL_PERFECTION, ToolPerfectionHandler())
        self.register_handler(Phase.WORLD_UNLOCK, WorldUnlockHandler())
        self.register_handler(Phase.MEGABASE_INIT, MegabaseInitHandler())
        
    def _get_current_world_identity(self) -> Optional[WorldIdentity]:
        """Fetch stable world identity from bridge state telemetry."""
        try:
            state = self.client.transport.dispatch("get_state", {})
            transport = self.client.transport
            server_address = None
            host = getattr(transport, "host", None)
            port = getattr(transport, "port", None)
            if host:
                server_address = f"{host}:{port}" if port is not None else str(host)
            identity = WorldIdentity.from_state(state, server_address=server_address)
            return identity if identity.available else None
        except Exception:
            return None

    def _get_current_seed(self) -> Optional[int]:
        """Backward-compatible seed accessor."""
        identity = self._get_current_world_identity()
        return identity.seed if identity else None
    
    
    def configure_baritone(self):
        """Configure Baritone settings for safety and performance."""
        print("Configuring Baritone settings...")
        settings = [
            "assumeWalkOnLava false",
            "assumeWalkOnWater false",
            "costLava 200", # Extremely high cost to avoid lava
            "allowParkour true",
            "allowSprint true",
            "maxFallHeightNoWater 3",
            "chatDebug false", # Reduce spam
            "freeLook true", # Allow looking around while pathing
            "allowVines false" # Prevent getting stuck in vines
        ]
        for setting in settings:
            self.client.transport.dispatch("chat", {"message": f"#set {setting}"})
            time.sleep(0.1)
            
        # Note: Lava avoidance handled by costLava setting above


    def load_or_start(self) -> Phase:
        """
        Load checkpoint or start fresh.
        
        Returns:
            Starting phase
        """
        identity = self._get_current_world_identity()
        self.state.bind_world_identity(identity)
        seed = identity.seed if identity else None
        if self.state.load_checkpoint(
            current_seed=seed,
            current_world_identity=identity,
        ):
            print(f"Resumed from checkpoint: {self.state.get_current_phase().name}")
            if seed:
                print(f"  World seed: {seed}")
        else:
            print("Starting fresh automation")

        try:
            from ..common.storage_catalog import seed_from_state

            seeded = seed_from_state(self.client, self.state)
            if seeded:
                print(f"Storage catalog loaded {seeded} checkpointed container landmark(s)")
        except Exception as exc:
            print(f"Storage catalog seed deferred: {exc}")
        
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
            
        # Baritone settings are applied by the spawn-bootstrap phase before
        # pathing starts.  Reapplying them here from the bridge worker can race
        # Baritone's tick thread (and has caused ConcurrentModificationException
        # crashes), especially when resuming directly into a later phase.
            
        # Initialize dynamic resources
        self.resources.initialize_recipes()
        
        # Record Spawn Location
        try:
             state = self.client.transport.dispatch("get_state", {})
             pos = state.get("block_position", {})
             if pos:
                 self.state.add_location("spawn", int(pos.get("x")), int(pos.get("y")), int(pos.get("z")), tags=["start"], client=self.client)
        except:
             pass
        
        # Start background systems
        for system in self.systems:
            system.start()
        
        print(f"\n{'#'*60}")
        print(f"#  EndGame Automator Started")
        print(f"#  Current Phase: {self.state.get_current_phase().name}")
        print(f"#  Overall Progress: {self.state.get_overall_progress()*100:.1f}%")
        print(f"{'#'*60}\n")
        
        try:
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

        finally:
             self.stop()


    def run_suite(self, suite_name: str) -> bool:
        """
        Run a specific test suite/mission action.
        
        Args:
            suite_name: Name of the suite (e.g. "T900")
            
        Returns:
            True if success
        """
        if suite_name not in SUITES:
            print(f"Error: Unknown suite '{suite_name}'")
            return False
            
        action_class = SUITES[suite_name]
        action = action_class()
        
        print(f"\n>>> Starting Suite: {suite_name} ({action.__class__.__name__})")
        
        self._running = True
        # Suites that need non-default Baritone settings should apply them as a
        # phase action before beginning pathing, rather than mutating settings
        # here while the Baritone tick thread may already be active.
        self.resources.initialize_recipes()
        
        # Start background systems
        for system in self.systems:
            system.start()
            
        try:
            context = ActionContext(
                client=self.client,
                state=self.state,
                resources=self.resources
            )
            
            result = action.execute(context)
            
            if result.success:
                print(f"\n>>> Suite {suite_name} COMPLETED SUCCESSFULLY!")
                return True
            else:
                print(f"\n!!! Suite {suite_name} FAILED: {result.reason}")
                return False
                
        except KeyboardInterrupt:
            print("\nSuite execution interrupted.")
            return False
        except Exception as e:
            print(f"\nError executing suite: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            self.stop()
    
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
        identity = self._get_current_world_identity()
        seed = identity.seed if identity else None
        path = self.state.save_checkpoint(
            inventory, world_seed=seed, world_identity=identity
        )
        print(f"Checkpoint saved: {path}")
    
    def _handle_death_recovery(self) -> bool:
        """Handle death and stop the run if recovery cannot be proven safe."""
        # Use modular death recovery action
        context = ActionContext(
            client=self.client,
            state=self.state,  # StateManager instance
            resources=self.resources
        )

        action = DeathRecoveryAction()
        result = action.execute(context)

        if result.success and result.message == "No death detected":
            return False

        if not result.success:
            # Previously this returned True, causing one loop iteration to be
            # skipped; on the following iteration the alive player resumed the
            # phase with a partial inventory.  A failed recovery is a hard
            # terminal condition for this supervised run.
            print(f"Death recovery failed: {result.message}. Stopping automation.")
            self._running = False
            return True

        # A successful recovery still skips the interrupted phase iteration so
        # every phase starts from a fresh state/inventory snapshot.
        return True

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
