"""
Dependency injection container for managing component instantiation and wiring.

This module provides a lightweight dependency injection container that supports:
- Singleton and transient service registration
- Automatic dependency resolution
- Service scoping for request-specific instances
- Factory-based service creation

Example usage:
    container = DependencyInjectionContainer()
    container.register_singleton(Client, instance=client)
    container.register_singleton(ResourceManager, factory=resource_manager_factory)

    # Resolve services
    client = container.get_service(Client)
    resources = container.get_service(ResourceManager)
"""

from typing import Dict, Type, TypeVar, Any, Optional, Callable, Protocol, Union
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

T = TypeVar('T')
U = TypeVar('U')


class IServiceProvider(Protocol[T]):
    """
    Protocol for service providers in the DI container.

    Defines the interface for retrieving services by type and checking
    service availability.
    """

    def get_service(self, service_type: Type[T]) -> T:
        """
        Get a service instance by type.

        Args:
            service_type: The type of service to retrieve

        Returns:
            Service instance of the requested type

        Raises:
            ValueError: If the service type is not registered
        """
        ...

    def has_service(self, service_type: Type[T]) -> bool:
        """
        Check if a service type is registered.

        Args:
            service_type: The service type to check

        Returns:
            True if the service is registered, False otherwise
        """
        ...


class IServiceFactory(Protocol[T]):
    """
    Protocol for service factories.

    Defines how services can be created using dependency injection.
    """

    def create_service(self, provider: IServiceProvider) -> T:
        """
        Create a service instance using the provider for dependencies.

        Args:
            provider: Service provider for resolving dependencies

        Returns:
            Newly created service instance
        """
        ...


@dataclass
class ServiceRegistration:
    """Registration information for a service."""
    service_type: Type
    implementation_type: Optional[Type] = None
    factory: Optional[Callable[[IServiceProvider], Any]] = None
    instance: Optional[Any] = None
    singleton: bool = True
    dependencies: Dict[str, Type] = field(default_factory=dict)


class DependencyInjectionContainer(IServiceProvider):
    """
    Lightweight dependency injection container for managing component dependencies.

    Provides singleton and transient service registration with automatic dependency resolution.
    """

    def __init__(self):
        self._registrations: Dict[Type, ServiceRegistration] = {}
        self._singletons: Dict[Type, Any] = {}

    def register_singleton(self, service_type: Type[T], implementation_type: Optional[Type[T]] = None,
                          factory: Optional[Callable[[IServiceProvider], T]] = None,
                          instance: Optional[T] = None) -> 'DependencyInjectionContainer':
        """
        Register a singleton service.

        Args:
            service_type: The service interface/contract type
            implementation_type: The concrete implementation type (optional if factory provided)
            factory: Factory function for creating the service (optional)
            instance: Pre-created instance to use (optional)
        """
        if instance is not None:
            self._singletons[service_type] = instance
        else:
            self._registrations[service_type] = ServiceRegistration(
                service_type=service_type,
                implementation_type=implementation_type,
                factory=factory,
                singleton=True
            )
        return self

    def register_transient(self, service_type: Type[T], implementation_type: Optional[Type[T]] = None,
                          factory: Optional[Callable[[IServiceProvider], T]] = None) -> 'DependencyInjectionContainer':
        """
        Register a transient service (new instance each time).

        Args:
            service_type: The service interface/contract type
            implementation_type: The concrete implementation type (optional if factory provided)
            factory: Factory function for creating the service (optional)
        """
        self._registrations[service_type] = ServiceRegistration(
            service_type=service_type,
            implementation_type=implementation_type,
            factory=factory,
            singleton=False
        )
        return self

    def has_service(self, service_type: Type[T]) -> bool:
        """Check if a service type is registered."""
        return service_type in self._registrations or service_type in self._singletons

    def get_service(self, service_type: Type[T]) -> T:
        """
        Get a service instance by type, resolving dependencies automatically.

        Args:
            service_type: The service type to retrieve

        Returns:
            Service instance

        Raises:
            ValueError: If service type is not registered
        """
        # Check for pre-registered singleton instance
        if service_type in self._singletons:
            return self._singletons[service_type]

        # Check for registration
        if service_type not in self._registrations:
            raise ValueError(f"Service type {service_type} is not registered")

        registration = self._registrations[service_type]

        # Return cached singleton if available
        if registration.singleton and service_type in self._singletons:
            return self._singletons[service_type]

        # Create new instance
        instance = self._create_instance(registration)

        # Cache singleton instances
        if registration.singleton:
            self._singletons[service_type] = instance

        return instance

    def _create_instance(self, registration: ServiceRegistration) -> Any:
        """Create a service instance from registration."""
        if registration.factory is not None:
            return registration.factory(self)
        elif registration.implementation_type is not None:
            return self._instantiate_type(registration.implementation_type)
        else:
            raise ValueError(f"No factory or implementation type for service {registration.service_type}")

    def _instantiate_type(self, implementation_type: Type[T]) -> T:
        """Instantiate a type, resolving constructor dependencies."""
        try:
            # Try to create with dependency injection
            # This is a simplified version - in a full DI container,
            # you'd analyze the constructor signature
            return implementation_type()
        except TypeError:
            # If no suitable constructor, try default instantiation
            raise ValueError(f"Cannot instantiate {implementation_type} - check constructor dependencies")

    def create_scope(self) -> 'ServiceScope':
        """Create a new service scope for scoped services."""
        return ServiceScope(self)


class ServiceScope(IServiceProvider):
    """A scoped service provider that can have its own service instances."""

    def __init__(self, parent: DependencyInjectionContainer):
        self._parent = parent
        self._scoped_instances: Dict[Type, Any] = {}

    def has_service(self, service_type: Type[T]) -> bool:
        """Check if service is available in this scope or parent."""
        return service_type in self._scoped_instances or self._parent.has_service(service_type)

    def get_service(self, service_type: Type[T]) -> T:
        """Get service from this scope or parent."""
        if service_type in self._scoped_instances:
            return self._scoped_instances[service_type]

        # For transient services, create new instances per scope
        if service_type in self._parent._registrations:
            registration = self._parent._registrations[service_type]
            if not registration.singleton:
                instance = self._parent._create_instance(registration)
                self._scoped_instances[service_type] = instance
                return instance

        # Fall back to parent
        return self._parent.get_service(service_type)


# Factory functions for common service registration patterns
def action_factory(service_provider: IServiceProvider):
    """Factory for creating action instances with dependencies."""
    from baritone_client.actions import (
        MovementAction, InventoryAction, CraftingAction, CombatAction,
        RecoveryAction, SensingAction, TravelAction
    )

    # Return a dict of actions for easy access
    return {
        'movement': MovementAction(),
        'inventory': InventoryAction(),
        'crafting': CraftingAction(),
        'combat': CombatAction(),
        'recovery': RecoveryAction(),
        'sensing': SensingAction(),
        'travel': TravelAction(),
    }


def resource_manager_factory(service_provider: IServiceProvider):
    """Factory for ResourceManager with dependencies."""
    from baritone_client.automator.resource_manager import ResourceManager
    return ResourceManager()


def state_manager_factory(service_provider: IServiceProvider):
    """Factory for StateManager with dependencies."""
    from baritone_client.automator.state_manager import StateManager
    return StateManager()


def create_default_container() -> DependencyInjectionContainer:
    """
    Create a container with default service registrations for the baritone client.

    Returns:
        Configured dependency injection container
    """
    container = DependencyInjectionContainer()

    # Register core services
    container.register_singleton(dict, factory=action_factory)
    container.register_singleton(type(lambda: None), factory=resource_manager_factory)  # ResourceManager type
    container.register_singleton(type(lambda: None), factory=state_manager_factory)     # StateManager type

    return container