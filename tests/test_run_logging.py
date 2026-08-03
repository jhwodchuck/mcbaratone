"""The runner must actually surface the client library's own logs.

Nothing configured logging, so Python's last-resort handler dropped
everything below WARNING and every logger.info in the common/ gameplay layer
was discarded. Live 2026-08-03: three bots sat motionless inside
find_nether_fortress and not one of its INFO lines -- including "Starting
Nether fortress scan" -- reached any run log, so a stall was indistinguishable
from normal work.
"""

import importlib.util
import logging
import pathlib
import sys


def _load_runner():
    root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    spec = importlib.util.spec_from_file_location(
        "spawn_to_dragon_runner", root / "spawn_to_dragon.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_client_info_logs_reach_stdout(capsys):
    runner = _load_runner()
    package = logging.getLogger("baritone_client")
    original_level, original_handlers = package.level, list(package.handlers)
    root = logging.getLogger()
    root_level, root_handlers = root.level, list(root.handlers)
    try:
        root.handlers = []          # a fresh process has none
        runner._configure_logging()
        logging.getLogger("baritone_client.common.nether").info(
            "Starting Nether fortress scan"
        )
        assert "Starting Nether fortress scan" in capsys.readouterr().out, (
            "the gameplay layer's INFO logs must reach the run log"
        )
    finally:
        package.setLevel(original_level)
        package.handlers = original_handlers
        root.setLevel(root_level)
        root.handlers = root_handlers


def test_third_party_info_logs_stay_out_of_the_run_log(capsys):
    """Scoped to the package, so a chatty dependency cannot bury the run log."""
    runner = _load_runner()
    root = logging.getLogger()
    root_level, root_handlers = root.level, list(root.handlers)
    try:
        root.handlers = []          # a fresh process has none
        runner._configure_logging()
        logging.getLogger("some_noisy_dependency").info("chatter")
        assert "chatter" not in capsys.readouterr().out
    finally:
        root.setLevel(root_level)
        root.handlers = root_handlers
