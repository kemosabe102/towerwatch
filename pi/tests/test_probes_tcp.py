"""Characterization tests for probes/tcp.py — 4 tests."""
import socket
from unittest.mock import patch, MagicMock

import pytest


def test_tcp_connect_success_returns_ms():
    import probes.tcp as tcp_mod

    mock_sock = MagicMock(spec=socket.socket)
    with patch("probes.tcp.socket.socket", return_value=mock_sock):
        with patch("probes.tcp.time.perf_counter", side_effect=[0.0, 0.015]):
            result = tcp_mod.measure_tcp_connect()
    assert result == 15
    mock_sock.connect.assert_called_once()
    mock_sock.close.assert_called_once()

def test_tcp_connect_refused_returns_zero():
    import probes.tcp as tcp_mod

    mock_sock = MagicMock(spec=socket.socket)
    mock_sock.connect.side_effect = ConnectionRefusedError("refused")
    with patch("probes.tcp.socket.socket", return_value=mock_sock):
        result = tcp_mod.measure_tcp_connect()
    assert result == 0

def test_tcp_connect_timeout_returns_zero():
    import probes.tcp as tcp_mod

    mock_sock = MagicMock(spec=socket.socket)
    mock_sock.connect.side_effect = socket.timeout("timed out")
    with patch("probes.tcp.socket.socket", return_value=mock_sock):
        result = tcp_mod.measure_tcp_connect()
    assert result == 0

def test_tcp_socket_closed_after_failure():
    """Socket must be closed even when connect raises."""
    import probes.tcp as tcp_mod

    mock_sock = MagicMock(spec=socket.socket)
    mock_sock.connect.side_effect = OSError("network unreachable")
    with patch("probes.tcp.socket.socket", return_value=mock_sock):
        tcp_mod.measure_tcp_connect()
    mock_sock.close.assert_called_once()
