#!/usr/bin/env bash
# Install multicats into the slurmctld container.
# Requires the cluster to already be running (./cluster/start.sh).
set -eou pipefail

ROOT="$(dirname "$0")/.."
docker exec slurmctld mkdir -p /tmp/multicats
for item in pyproject.toml multicats tests; do
  docker cp "$ROOT/$item" slurmctld:/tmp/multicats/
done
docker exec -e UV_SYSTEM_PYTHON=1 slurmctld uv pip install /tmp/multicats
