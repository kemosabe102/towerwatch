"""Tests for TCPProbe — no patch, fakes injected directly."""
import socket
import sys
from pathlib import Path

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))

from tests.fakes import FakeClock, FakeSocket, fake_socket_factory


def test_tcp_connect_success_returns_ms():
    from probes.tcp import TCPProbe
    factory = fake_socket_factory()
    probe = TCPProbe(
        socket_factory=factory,
        clock=FakeClock(perf=[0.0, 0.015]),
        host="127.0.0.1", port=443, timeout_s=3,
    )
    assert probe.measure() == 15
    assert factory.sockets[0].connect_calls == [("127.0.0.1", 443)]
    assert factory.sockets[0].closed is True


def test_tcp_connect_refused_returns_zero():
    from probes.tcp import TCPProbe
    factory = fake_socket_factory(connect_raises=ConnectionRefusedError("refused"))
    probe = TCPProbe(
        socket_factory=factory,
        clock=FakeClock(perf=[0.0]),
        host="127.0.0.1", port=443,
    )
    assert probe.measure() == 0
    assert factory.sockets[0].closed is True


def test_tcp_connect_timeout_returns_zero():
    from probes.tcp import TCPProbe
    factory = fake_socket_factory(connect_raises=socket.timeout("timed out"))
    probe = TCPProbe(
        socket_factory=factory,
        clock=FakeClock(perf=[0.0]),
    )
    assert probe.measure() == 0


def test_tcp_socket_closed_after_failure():
    """Socket must be closed even when connect raises."""
    from probes.tcp import TCPProbe
    factory = fake_socket_factory(connect_raises=OSError("network unreachable"))
    probe = TCPProbe(
        socket_factory=factory,
        clock=FakeClock(perf=[0.0]),
    )
    probe.measure()
    assert factory.sockets[0].closed is True


def test_tcp_timeout_applied_to_socket():
    from probes.tcp import TCPProbe
    factory = fake_socket_factory()
    probe = TCPProbe(
        socket_factory=factory,
        clock=FakeClock(perf=[0.0, 0.01]),
        timeout_s=7,
    )
    probe.measure()
    assert factory.sockets[0].timeout == 7
