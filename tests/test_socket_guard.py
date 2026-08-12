"""Regression tests for the default offline network boundary."""

from __future__ import annotations

import socket
import warnings

import pytest
from pytest_socket import SocketConnectBlockedError


@pytest.mark.parametrize("attempt", [1, 2])
def test_outbound_connect_remains_blocked_for_each_test(attempt) -> None:
    """The per-test guard must survive pytest-socket's previous teardown."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with pytest.raises(SocketConnectBlockedError):
                connection.connect(("192.0.2.1", 9))
