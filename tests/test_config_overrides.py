"""Tests for the per-site credential override loaders in config.py."""

import sys
import types

import pytest


@pytest.fixture
def fake_credentials():
    """Install a fake `towerwatch.credentials` module for the duration of the test
    and remove it after, so other tests get the real one back.

    Both `sys.modules` AND the `towerwatch` package's bound attribute have to
    point at the fake — `from towerwatch import credentials` inside
    _load_int_credential resolves the package attribute, not sys.modules.
    """
    import towerwatch

    saved_modules = sys.modules.get("towerwatch.credentials")
    saved_attr = getattr(towerwatch, "credentials", None)
    fake = types.ModuleType("towerwatch.credentials")
    sys.modules["towerwatch.credentials"] = fake
    towerwatch.credentials = fake  # type: ignore[attr-defined]
    yield fake
    if saved_modules is not None:
        sys.modules["towerwatch.credentials"] = saved_modules
    else:
        del sys.modules["towerwatch.credentials"]
    if saved_attr is not None:
        towerwatch.credentials = saved_attr  # type: ignore[attr-defined]
    else:
        del towerwatch.credentials  # type: ignore[attr-defined]


def test_int_credential_returns_fallback_when_attribute_missing(fake_credentials):
    from towerwatch.config import _load_int_credential

    assert _load_int_credential("DOES_NOT_EXIST", 42) == 42


def test_int_credential_returns_fallback_when_attribute_is_none(fake_credentials):
    fake_credentials.MY_OVERRIDE = None
    from towerwatch.config import _load_int_credential

    assert _load_int_credential("MY_OVERRIDE", 42) == 42


def test_int_credential_returns_override_when_set(fake_credentials):
    fake_credentials.MY_OVERRIDE = 7
    from towerwatch.config import _load_int_credential

    assert _load_int_credential("MY_OVERRIDE", 42) == 7


def test_windows_credential_returns_none_fallback_when_missing(fake_credentials):
    from towerwatch.config import _load_windows_credential

    assert _load_windows_credential("DOES_NOT_EXIST", None) is None


def test_windows_credential_returns_fallback_when_explicit_none(fake_credentials):
    fake_credentials.MY_WINDOWS = None
    from towerwatch.config import _load_windows_credential

    assert _load_windows_credential("MY_WINDOWS", None) is None


def test_windows_credential_parses_list_of_tuples(fake_credentials):
    fake_credentials.MY_WINDOWS = [(6, 10), (11, 14), (17, 21)]
    from towerwatch.config import _load_windows_credential

    result = _load_windows_credential("MY_WINDOWS", None)
    assert result == [(6, 10), (11, 14), (17, 21)]


def test_windows_credential_coerces_strings_to_ints(fake_credentials):
    """Defensive: someone writes ('6', '10') in their credentials file."""
    fake_credentials.MY_WINDOWS = [("6", "10"), ("17", "21")]
    from towerwatch.config import _load_windows_credential

    result = _load_windows_credential("MY_WINDOWS", None)
    assert result is not None
    assert result == [(6, 10), (17, 21)]
    assert all(isinstance(s, int) and isinstance(e, int) for s, e in result)


def test_str_list_credential_returns_fallback_when_attribute_missing(fake_credentials):
    from towerwatch.config import _load_str_list_credential

    assert _load_str_list_credential("DOES_NOT_EXIST", ["8.8.8.8"]) == ["8.8.8.8"]


def test_str_list_credential_returns_fallback_when_explicit_none(fake_credentials):
    fake_credentials.DNS_TARGETS_OVERRIDE = None
    from towerwatch.config import _load_str_list_credential

    assert _load_str_list_credential("DNS_TARGETS_OVERRIDE", ["8.8.8.8"]) == ["8.8.8.8"]


def test_str_list_credential_returns_fallback_when_empty_list(fake_credentials):
    """An empty override is treated as 'unset' — fall back rather than probe
    zero resolvers (which would silently disable the DNS probe)."""
    fake_credentials.DNS_TARGETS_OVERRIDE = []
    from towerwatch.config import _load_str_list_credential

    assert _load_str_list_credential("DNS_TARGETS_OVERRIDE", ["8.8.8.8"]) == ["8.8.8.8"]


def test_str_list_credential_returns_override_when_set(fake_credentials):
    fake_credentials.DNS_TARGETS_OVERRIDE = ["8.8.8.8", "1.1.1.1"]
    from towerwatch.config import _load_str_list_credential

    result = _load_str_list_credential("DNS_TARGETS_OVERRIDE", ["9.9.9.9"])
    assert result == ["8.8.8.8", "1.1.1.1"]


def test_str_list_credential_coerces_entries_to_str(fake_credentials):
    """Defensive: entries arrive as non-strings; nameserver field expects str."""
    fake_credentials.DNS_TARGETS_OVERRIDE = [8, "1.1.1.1"]
    from towerwatch.config import _load_str_list_credential

    result = _load_str_list_credential("DNS_TARGETS_OVERRIDE", ["9.9.9.9"])
    assert result == ["8", "1.1.1.1"]
    assert all(isinstance(v, str) for v in result)
