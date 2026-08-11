"""Local, hand-rolled fakes that model enough of a subsystem to make its
failure modes reachable from a test -- as opposed to conftest.py's
MockTransport, which answers every unmodelled route with a permissive
{"status": "ok"} and therefore cannot distinguish a real success from a
silent no-op.

Kept local to tests/fakes/ rather than folded into conftest.py deliberately:
a shared default is a world that agrees with everything, and the next
contributor who needs a different answer will retune it out from under
whichever test relied on the old one. See tests/test_terraform.py for the
reasoning this was born from.
"""
