#!/bin/sh
# Requires network on the host; this script never changes an existing checkout.
# Usage: sh examples/real-projects.sh [PYTHON [OUTPUT_PARENT]]
set -eu
if [ "$#" -gt 2 ]; then
    echo 'usage: real-projects.sh [PYTHON [OUTPUT_PARENT]]' >&2
    exit 2
fi
exec "${1:-python3}" "$(dirname "$0")/real_projects.py" "${2:-${TMPDIR:-/tmp}}"
