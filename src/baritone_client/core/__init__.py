from .client import Client
from .facades import (
    CommandFacade,
    ProcessFacade,
    GoalManager,
    SettingsFacade,
    MissionFacade,
    SchematicManager,
)
from .dependency_injection import (
    ComponentRegistry,
    ConfigurationBuilder,
    create_basic_automation_config,
    create_advanced_automation_config,
    inject_dependencies,
)

__all__ = [
    "Client",
    "CommandFacade",
    "ProcessFacade",
    "GoalManager",
    "SettingsFacade",
    "MissionFacade",
    "SchematicManager",
    "ComponentRegistry",
    "ConfigurationBuilder",
    "create_basic_automation_config",
    "create_advanced_automation_config",
    "inject_dependencies",
]