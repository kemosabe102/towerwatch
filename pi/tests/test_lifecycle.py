"""Tests for lifecycle.py — no patch, FakeSignal injected."""
import signal as real_signal
import sys
from pathlib import Path

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))

from tests.fakes import FakeSignal


def test_sigterm_handler_flips_shutdown_requested():
    """Fake signal module records the handler; invoking it flips state."""
    from lifecycle import RuntimeState, install_signal_handlers

    state = RuntimeState()
    fake = FakeSignal()
    install_signal_handlers(state, is_windows=False, signal_module=fake)

    # Handler was registered for SIGTERM
    assert real_signal.SIGTERM in fake.handlers
    handler = fake.handlers[real_signal.SIGTERM]

    # Invoking the handler flips the flag
    handler(real_signal.SIGTERM, None)
    assert state.shutdown_requested is True


def test_install_signal_handlers_noop_on_windows():
    from lifecycle import RuntimeState, install_signal_handlers

    state = RuntimeState()
    fake = FakeSignal()
    install_signal_handlers(state, is_windows=True, signal_module=fake)
    assert fake.handlers == {}


def test_install_signal_handlers_autodetects_platform():
    """Without is_windows arg, detection uses the module's IS_WINDOWS constant."""
    from lifecycle import RuntimeState, install_signal_handlers, IS_WINDOWS

    state = RuntimeState()
    fake = FakeSignal()
    install_signal_handlers(state, signal_module=fake)
    if IS_WINDOWS:
        assert fake.handlers == {}
    else:
        assert real_signal.SIGTERM in fake.handlers


def test_configure_logging_idempotent():
    from lifecycle import configure_logging
    configure_logging()
    configure_logging()


def test_runtime_state_defaults():
    from lifecycle import RuntimeState
    s = RuntimeState()
    assert s.connected is True
    assert s.outage_start == 0
    assert s.outage_count == 0
    assert s.shutdown_requested is False
    assert s.metric_batch == []
