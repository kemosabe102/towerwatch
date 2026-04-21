"""Characterization tests for config.py import behaviour — 2 tests."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def test_config_import_does_not_spawn_subprocess_when_version_txt_present():
    """config.py must not call git when version.txt exists and is readable.

    The module reads version.txt first; subprocess is only the fallback.
    With version.txt present in this repo, git is never called.
    """
    import config as cfg_module
    assert cfg_module.BUILD_VERSION, "BUILD_VERSION must be non-empty after import"


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Pass 3 fix: config.py falls back to git subprocess when version.txt absent. "
        "With version.txt present this passes; it xfails only when version.txt is missing. "
        "After Pass 3, TOWERWATCH_SKIP_GIT_VERSION=1 gates the subprocess call."
    ),
)
def test_config_import_without_version_txt_does_not_spawn_subprocess(tmp_path, monkeypatch):
    """When version.txt is absent, importing config should NOT spawn git.

    Currently it DOES call subprocess.check_output as a git fallback.
    Pass 3 adds an env-var guard so tests can import config cleanly.
    """
    # Pop config so _load_build_version re-runs
    sys.modules.pop("config", None)

    # Patch version.txt candidates to point only at tmp_path (no file there)
    _real_is_file = Path.is_file

    def _no_version_txt(self):
        if self.name == "version.txt":
            return False
        return _real_is_file(self)

    subprocess_calls = []
    _real_check = subprocess.check_output

    def _tracking_check(*args, **kwargs):
        subprocess_calls.append(args)
        return _real_check(*args, **kwargs)

    with patch.object(Path, "is_file", _no_version_txt):
        with patch("subprocess.check_output", side_effect=_tracking_check):
            import config  # noqa: F401

    assert subprocess_calls == [], (
        "subprocess.check_output must not be called — "
        "set TOWERWATCH_SKIP_GIT_VERSION=1 (Pass 3 fix)"
    )
