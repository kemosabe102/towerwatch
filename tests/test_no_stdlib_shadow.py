"""Verify that renaming secrets.py → credentials.py removed the stdlib shadow."""


def test_stdlib_secrets_module_accessible():
    """import secrets must resolve to the Python stdlib, not our credentials file."""
    import secrets as stdlib_secrets

    # token_hex is a stdlib-only function — would NameError if our file shadowed it
    token = stdlib_secrets.token_hex(4)
    assert isinstance(token, str)
    assert len(token) == 8  # 4 bytes → 8 hex chars
