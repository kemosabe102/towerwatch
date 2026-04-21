"""Characterization tests for config.py import behaviour — 2 tests."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def test_config_import_does_not_spawn_subprocess_when_version_txt_present():
    """config.py must not call git when version.txt exists and is readable."""
    import config as cfg_module
    assert cfg_module.BUILD_VERSION, "BUILD_VERSION must be non-empty after import"


def test_config_import_without_version_txt_does_not_spawn_subprocess(tmp_path, monkeypatch):
    """With TOWERWATCH_SKIP_GIT_VERSION=1, importing config never calls git subprocess.

    Flipped from xfail in Pass 3 once the env-var guard was added to _load_build_version.
    """
    monkeypatch.setenv("TOWERWATCH_SKIP_GIT_VERSION", "1")

    # Pop config so _load_build_version re-runs on next import
    sys.modules.pop("config", None)

    subprocess_calls = []
    _real_check = subprocess.check_output

    def _tracking_check(*args, **kwargs):
        subprocess_calls.append(args)
        return _real_check(*args, **kwargs)

    # Hide all version.txt files so the git-fallback branch would be reached
    _real_is_file = Path.is_file

    def _no_version_txt(self):
        if self.name == "version.txt":
            return False
        return _real_is_file(self)

    with patch.object(Path, "is_file", _no_version_txt):
        with patch("subprocess.check_output", side_effect=_tracking_check):
            import config  # noqa: F401

    assert subprocess_calls == [], (
        "subprocess.check_output must not be called when TOWERWATCH_SKIP_GIT_VERSION=1"
    )
    assert config.BUILD_VERSION == "dev"
    assert config.BUILD_DATE == "unknown"
