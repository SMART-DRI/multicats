#!/usr/bin/env bash
# Start the Slurm cluster, run e2e tests, then tear down.
set -eou pipefail

CLUSTER_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$CLUSTER_DIR/.."

cleanup() {
  echo "--- Stopping cluster ---"
  pushd "$CLUSTER_DIR" >/dev/null
  docker compose down
  popd >/dev/null
}
trap cleanup EXIT

echo "--- Starting cluster ---"
pushd "$CLUSTER_DIR" >/dev/null
docker compose pull --quiet
docker compose up -d
popd >/dev/null

echo "--- Waiting for slurmctld ---"
until docker exec slurmctld sinfo &>/dev/null; do
  sleep 2
done

echo "--- Installing job submit plugin ---"
docker cp "$CLUSTER_DIR/job_submit.lua" slurmctld:/etc/slurm/job_submit.lua
docker exec slurmctld bash -c \
  "grep -q 'JobSubmitPlugins' /etc/slurm/slurm.conf \
   || echo 'JobSubmitPlugins=lua' >> /etc/slurm/slurm.conf"
docker restart slurmctld
echo "--- Waiting for slurmctld after restart ---"
until docker exec slurmctld sinfo &>/dev/null; do
  sleep 2
done
docker exec slurmctld scontrol create PartitionName=multicats Nodes=ALL State=UP

echo "--- Installing multicats ---"
docker exec slurmctld mkdir -p /tmp/multicats
for item in pyproject.toml multicats tests; do
  docker cp "$ROOT/$item" slurmctld:/tmp/multicats/
done
docker exec -e UV_SYSTEM_PYTHON=1 slurmctld uv pip install /tmp/multicats[dev]

echo "--- Running e2e tests ---"
docker exec -e UV_SYSTEM_PYTHON=1 slurmctld uv run pytest /tmp/multicats/tests -m e2e -v
