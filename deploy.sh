#!/bin/bash
# Backwards-compat shim. The deploy script is now cd.sh (paired with ci.sh).
# Kept so existing muscle memory and any external references still work.
exec bash "$(dirname "${BASH_SOURCE[0]}")/cd.sh" "$@"
