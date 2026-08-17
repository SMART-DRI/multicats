#!/usr/bin/env bash
# Run the full test suite (unit + e2e) inside slurmctld.
# Requires: ./cluster/start.sh && ./cluster/install_multicats.sh
set -eou pipefail
docker exec -e UV_SYSTEM_PYTHON=1 slurmctld uv run pytest /tmp/multicats/tests -m e2e -v
