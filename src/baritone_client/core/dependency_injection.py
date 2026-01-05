"""
Dependency injection system for modular component wiring.

Provides factories and configuration-based component instantiation
for flexible automation system assembly.
"""

from typing import Dict, Any, Type, Callable, Optional, Protocol, TypeVar
from dataclasses import dataclass, field
import inspect

from ..core.interfaces import IAction, ActionContext
from ..actions import (
    MovementAction, InventoryAction, CraftingAction, CombatAction,
    DeathRecoveryAction, SensingAction, TravelAction
)


T = TypeVar('T')


class IFactory(Protocol[T]):
    """Protocol for component factories."""

    def create(self, config: Dict[str, Any]) -> T:
        """Create an instance with the given configuration."""
        ...


@dataclass
class ComponentConfig:
    """Configuration for a component."""
    type: str
    params: Dict[str, Any] = field(default_factory=dict)
    dependencies: Dict[str, str] = field(default_factory=dict)  # name -> component_key


class ComponentRegistry:
    """
    Registry for component factories and configurations.

    Manages the creation and wiring of automation components.
    """

    def __init__(self):
        self._factories: Dict[str, IFactory] = {}
        self._configs: Dict[str, ComponentConfig] = {}
        self._instances: Dict[str, Any] = {}

        # Register default factories
        self._register_defaults()

    def _register_defaults(self):
        """Register default component factories."""
        self.register_factory("MovementAction", ActionFactory(MovementAction))
        self.register_factory("InventoryAction", ActionFactory(InventoryAction))
        self.register_factory("CraftingAction", ActionFactory(CraftingAction))
        self.register_factory("CombatAction", ActionFactory(CombatAction))
        self.register_factory("DeathRecoveryAction", ActionFactory(DeathRecoveryAction))
        self.register_factory("SensingAction", ActionFactory(SensingAction))
        self.register_factory("TravelAction", ActionFactory(TravelAction))

    def register_factory(self, component_type: str, factory: IFactory) -> None:
        """Register a factory for a component type."""
        self._factories[component_type] = factory

    def register_config(self, key: str, config: ComponentConfig) -> None:
        """Register configuration for a component."""
        self._configs[key] = config

    def get_component(self, key: str) -> Any:
        """Get or create a component instance."""
        if key in self._instances:
            return self._instances[key]

        if key not in self._configs:
            raise ValueError(f"No configuration found for component: {key}")

        config = self._configs[key]
        instance = self._create_component(config)
        self._instances[key] = instance
        return instance

    def _create_component(self, config: ComponentConfig) -> Any:
        """Create a component instance from configuration."""
        if config.type not in self._factories:
            raise ValueError(f"No factory registered for type: {config.type}")

        factory = self._factories[config.type]

        # Resolve dependencies
        resolved_params = config.params.copy()
        for param_name, dep_key in config.dependencies.items():
            resolved_params[param_name] = self.get_component(dep_key)

        return factory.create(resolved_params)

    def clear_instances(self) -> None:
        """Clear all cached instances."""
        self._instances.clear()


class ActionFactory(IFactory[IAction]):
    """Factory for creating action instances."""

    def __init__(self, action_class: Type[IAction]):
        self.action_class = action_class

    def create(self, config: Dict[str, Any]) -> IAction:
        """Create an action instance."""
        # Get the constructor signature
        sig = inspect.signature(self.action_class.__init__)

        # Filter config to only include parameters accepted by the constructor
        # Skip 'self' parameter
        valid_params = {}
        for param_name, param in sig.parameters.items():
            if param_name == 'self':
                continue
            if param_name in config:
                valid_params[param_name] = config[param_name]
            elif param.default == inspect.Parameter.empty and param.kind != inspect.Parameter.VAR_KEYWORD:
                raise ValueError(f"Required parameter '{param_name}' not provided for {self.action_class.__name__}")

        return self.action_class(**valid_params)


class PhaseFactory(IFactory['Phase']):
    """Factory for creating phase instances."""

    def create(self, config: Dict[str, Any]) -> 'Phase':
        """Create a phase instance."""
        from ..automator.phase_builder import PhaseBuilder

        name = config.get('name', 'Unnamed Phase')
        builder = PhaseBuilder(name)

        if 'description' in config:
            builder.description(config['description'])

        # Add actions
        if 'actions' in config:
            for action_name, action_config in config['actions'].items():
                action = ComponentRegistry().get_component(action_config['key'])
                builder.with_action(action_name, action)

        # Add steps
        if 'steps' in config:
            for step_config in config['steps']:
                step_name = step_config['name']
                action_key = step_config['action']
                action = ComponentRegistry().get_component(action_key)
                builder.add_step(
                    step_name,
                    lambda ctx, act=action: act.execute(ctx),
                    required=step_config.get('required', True),
                    retry_count=step_config.get('retry_count', 0),
                    timeout=step_config.get('timeout'),
                    dependencies=step_config.get('dependencies', [])
                )

        # Add requirements
        if 'requirements' in config:
            builder.with_requirements(config['requirements'])

        # Set strategy
        if 'strategy' in config:
            strategy_config = config['strategy']
            strategy = ComponentRegistry().get_component(strategy_config['key'])
            builder.with_strategy(strategy)

        return builder.build()


class StrategyFactory(IFactory['IPhaseStrategy']):
    """Factory for creating strategy instances."""

    def create(self, config: Dict[str, Any]) -> 'IPhaseStrategy':
        """Create a strategy instance."""
        from ..automator.phase_builder import (
            SequentialPhaseStrategy, ParallelPhaseStrategy,
            ConditionalPhaseStrategy, RetryPhaseStrategy,
            LoopPhaseStrategy, CompositePhaseStrategy
        )

        strategy_type = config.get('type', 'sequential')

        if strategy_type == 'sequential':
            return SequentialPhaseStrategy()
        elif strategy_type == 'parallel':
            return ParallelPhaseStrategy()
        elif strategy_type == 'conditional':
            condition = config['condition']  # Should be a callable
            true_strategy = ComponentRegistry().get_component(config['true_strategy'])
            false_strategy = config.get('false_strategy')
            if false_strategy:
                false_strategy = ComponentRegistry().get_component(false_strategy)
            return ConditionalPhaseStrategy(condition, true_strategy, false_strategy)
        elif strategy_type == 'retry':
            base_strategy = ComponentRegistry().get_component(config['base_strategy'])
            return RetryPhaseStrategy(
                base_strategy,
                max_attempts=config.get('max_attempts', 3),
                delay_between_attempts=config.get('delay', 1.0)
            )
        elif strategy_type == 'loop':
            base_strategy = ComponentRegistry().get_component(config['base_strategy'])
            condition = config['condition']  # Should be a callable
            return LoopPhaseStrategy(
                base_strategy,
                condition,
                max_iterations=config.get('max_iterations', 10)
            )
        elif strategy_type == 'composite':
            strategies = [ComponentRegistry().get_component(key) for key in config['strategies']]
            return CompositePhaseStrategy(
                strategies,
                pattern=config.get('pattern', 'sequence'),
                require_all_success=config.get('require_all_success', False)
            )
        else:
            raise ValueError(f"Unknown strategy type: {strategy_type}")


class AutomatorFactory(IFactory['Automator']):
    """Factory for creating automator instances."""

    def create(self, config: Dict[str, Any]) -> 'Automator':
        """Create an automator instance."""
        from ..automator.automator import Automator

        # Get client
        client_key = config.get('client')
        if client_key:
            client = ComponentRegistry().get_component(client_key)
        else:
            # Assume client is passed directly or created elsewhere
            client = config.get('client_instance')

        # Create automator
        automator = Automator(client)

        # Add phases
        if 'phases' in config:
            for phase_config in config['phases']:
                phase = ComponentRegistry().get_component(phase_config['key'])
                automator.add_phase(phase)

        # Set initial state
        if 'initial_state' in config:
            automator.state.update(config['initial_state'])

        return automator


class ConfigurationBuilder:
    """
    Fluent builder for component configurations.

    Allows declarative configuration of complex automation systems.
    """

    def __init__(self):
        self.registry = ComponentRegistry()
        self._configs: Dict[str, ComponentConfig] = {}

    def configure_action(self, key: str, action_type: str, **params) -> 'ConfigurationBuilder':
        """Configure an action component."""
        config = ComponentConfig(type=action_type, params=params)
        self._configs[key] = config
        return self

    def configure_phase(self, key: str, name: str, **params) -> 'ConfigurationBuilder':
        """Configure a phase component."""
        config_params = {'name': name}
        config_params.update(params)
        config = ComponentConfig(type='Phase', params=config_params)
        self._configs[key] = config
        return self

    def configure_strategy(self, key: str, strategy_type: str, **params) -> 'ConfigurationBuilder':
        """Configure a strategy component."""
        config_params = {'type': strategy_type}
        config_params.update(params)
        config = ComponentConfig(type='Strategy', params=config_params)
        self._configs[key] = config
        return self

    def configure_automator(self, key: str, client_key: str, **params) -> 'ConfigurationBuilder':
        """Configure an automator component."""
        config_params = {'client': client_key}
        config_params.update(params)
        config = ComponentConfig(type='Automator', params=config_params)
        self._configs[key] = config
        return self

    def with_dependency(self, component_key: str, param_name: str, dependency_key: str) -> 'ConfigurationBuilder':
        """Add a dependency to a component."""
        if component_key in self._configs:
            self._configs[component_key].dependencies[param_name] = dependency_key
        return self

    def build(self) -> ComponentRegistry:
        """Build the component registry with all configurations."""
        for key, config in self._configs.items():
            self.registry.register_config(key, config)
        return self.registry


# Convenience functions for common configurations

def create_basic_automation_config(client) -> ComponentRegistry:
    """Create a basic automation configuration."""
    builder = ConfigurationBuilder()

    # Configure actions
    builder.configure_action('movement', 'MovementAction')
    builder.configure_action('inventory', 'InventoryAction')
    builder.configure_action('crafting', 'CraftingAction')
    builder.configure_action('combat', 'CombatAction')

    # Configure strategies
    builder.configure_strategy('sequential', 'sequential')

    # Configure phases
    builder.configure_phase(
        'gathering',
        'Resource Gathering',
        description='Gather basic resources',
        actions={
            'movement': {'key': 'movement'},
            'inventory': {'key': 'inventory'},
            'crafting': {'key': 'crafting'}
        },
        steps=[
            {
                'name': 'explore',
                'action': 'movement',
                'required': True
            },
            {
                'name': 'collect',
                'action': 'inventory',
                'required': False
            }
        ],
        strategy={'key': 'sequential'}
    )

    # Configure automator
    builder.configure_automator(
        'automator',
        client_key='client',
        phases=[{'key': 'gathering'}],
        client_instance=client
    )

    return builder.build()


def create_advanced_automation_config(client) -> ComponentRegistry:
    """Create an advanced automation configuration with complex workflows."""
    builder = ConfigurationBuilder()

    # Configure actions
    builder.configure_action('movement', 'MovementAction')
    builder.configure_action('inventory', 'InventoryAction')
    builder.configure_action('crafting', 'CraftingAction')
    builder.configure_action('combat', 'CombatAction')
    builder.configure_action('recovery', 'DeathRecoveryAction')

    # Configure strategies
    builder.configure_strategy('sequential', 'sequential')
    builder.configure_strategy('parallel', 'parallel')
    builder.configure_strategy('retry_sequential', 'retry', base_strategy='sequential', max_attempts=3)

    # Configure conditional strategies
    def health_check(context):
        # Example condition - check if health is low
        return context.state.player_health > 10

    builder.configure_strategy(
        'conditional_combat',
        'conditional',
        condition=health_check,
        true_strategy='sequential',
        false_strategy='parallel'  # Use parallel when health is low for speed
    )

    # Configure composite strategy
    builder.configure_strategy(
        'complex_workflow',
        'composite',
        strategies=['retry_sequential', 'conditional_combat'],
        pattern='sequence',
        require_all_success=True
    )

    # Configure phases
    builder.configure_phase(
        'combat_phase',
        'Advanced Combat',
        actions={
            'movement': {'key': 'movement'},
            'combat': {'key': 'combat'},
            'recovery': {'key': 'recovery'}
        },
        steps=[
            {
                'name': 'engage',
                'action': 'combat',
                'required': True,
                'retry_count': 2
            },
            {
                'name': 'recover',
                'action': 'recovery',
                'required': False,
                'dependencies': ['engage']
            }
        ],
        strategy={'key': 'conditional_combat'}
    )

    builder.configure_phase(
        'gathering_phase',
        'Advanced Gathering',
        actions={
            'movement': {'key': 'movement'},
            'inventory': {'key': 'inventory'},
            'crafting': {'key': 'crafting'}
        },
        strategy={'key': 'complex_workflow'}
    )

    # Configure workflow
    builder.configure_automator(
        'advanced_automator',
        client_key='client',
        phases=[
            {'key': 'gathering_phase'},
            {'key': 'combat_phase'}
        ],
        client_instance=client
    )

    return builder.build()


# Backward compatibility helpers

def inject_dependencies(target_class: Type[T], **dependencies) -> Callable[..., T]:
    """
    Create a factory function that injects dependencies into a class.

    Useful for backward compatibility with existing code.
    """
    def factory(*args, **kwargs):
        # Merge injected dependencies with provided kwargs
        all_kwargs = {**dependencies, **kwargs}

        # Get constructor signature
        sig = inspect.signature(target_class.__init__)

        # Filter to only valid parameters
        valid_kwargs = {}
        for param_name, param in sig.parameters.items():
            if param_name == 'self':
                continue
            if param_name in all_kwargs:
                valid_kwargs[param_name] = all_kwargs[param_name]
            elif param.default == inspect.Parameter.empty and param.kind != inspect.Parameter.VAR_KEYWORD:
                raise ValueError(f"Required parameter '{param_name}' not provided")

        return target_class(*args, **valid_kwargs)

    return factory