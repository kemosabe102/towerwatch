"""Tests for config._load_build_version — no patch, deps injected directly."""
import sys
from pathlib import Path

_PI = Path(__file__).resolve().parents[1]
if str(_PI) not in sys.path:
    sys.path.insert(0, str(_PI))


def test_config_import_does_not_spawn_subprocess_when_version_txt_present():
    """config.py must have a non-empty BUILD_VERSION after import."""
    import config as cfg_module
    assert cfg_module.BUILD_VERSION, "BUILD_VERSION must be non-empty after import"


def test_load_build_version_skips_subprocess_when_env_set(tmp_path):
    """With TOWERWATCH_SKIP_GIT_VERSION=1, _load_build_version never calls the
    check_output dependency — no subprocess spawned."""
    import config as cfg_module

    subprocess_calls = []

    def _tracking_check(*args, **kwargs):
        subprocess_calls.append(args)
        raise AssertionError("subprocess.check_output must not be called")

    # Candidates that don't exist → forces the env-var branch
    missing = tmp_path / "definitely_not_here" / "version.txt"

    version, build_date = cfg_module._load_build_version(
        candidates=[missing],
        env={"TOWERWATCH_SKIP_GIT_VERSION": "1"},
        check_output=_tracking_check,
    )
    assert subprocess_calls == []
    assert version == "dev"
    assert build_date == "unknown"


def test_load_build_version_reads_version_txt_when_present(tmp_path):
    """When version.txt exists and is readable, the loader returns its contents."""
    import config as cfg_module
    vfile = tmp_path / "version.txt"
    vfile.write_text("abc1234 2026-01-01T00:00:00Z\n", encoding="utf-8")

    version, build_date = cfg_module._load_build_version(
        candidates=[vfile],
        env={},
        check_output=lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("must not fall through to git")),
    )
    assert version == "abc1234"
    assert build_date == "2026-01-01T00:00:00Z"


def test_load_build_version_falls_back_to_git_when_env_absent(tmp_path):
    """No version.txt, no env var → falls back to check_output (git)."""
    import config as cfg_module

    class _FakeOut:
        def __init__(self, text):
            self._text = text

        def decode(self):
            return self._text

    def _check(cmd, **kwargs):
        if "rev-parse" in cmd:
            return _FakeOut("feedbee\n")
        if "log" in cmd:
            return _FakeOut("2026-02-02T00:00:00Z\n")
        raise AssertionError(f"unexpected cmd: {cmd}")

    missing = tmp_path / "nope" / "version.txt"
    version, build_date = cfg_module._load_build_version(
        candidates=[missing],
        env={},
        check_output=_check,
    )
    assert version == "feedbee"
    assert build_date == "2026-02-02T00:00:00Z"


def test_load_build_version_git_failure_returns_dev(tmp_path):
    """If git subprocess raises, we get the dev/unknown sentinel."""
    import config as cfg_module

    def _check(*args, **kwargs):
        raise FileNotFoundError("git not installed")

    missing = tmp_path / "nope" / "version.txt"
    version, build_date = cfg_module._load_build_version(
        candidates=[missing],
        env={},
        check_output=_check,
    )
    assert version == "dev"
    assert build_date == "unknown"
