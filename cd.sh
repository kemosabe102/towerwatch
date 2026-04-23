#!/bin/bash
# Shim — kept for muscle memory. The real script is scripts/deploy.sh.
exec "$(dirname "$0")/scripts/deploy.sh" "$@"
