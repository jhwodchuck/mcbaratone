"""Opt-in operations modules for supervised Minecraft bots.

Import concrete submodules explicitly.  Keeping this package initializer free
of live-operation imports lets offline state-machine and provisioning helpers
remain usable without loading RCON, commissioning, or fleet-control code.
"""

__all__: list[str] = []
