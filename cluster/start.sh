#!/usr/bin/env bash
set -eou pipefail
pushd "$(dirname "$0")"
docker compose pull
docker compose up -d
echo "Waiting for slurmctld to be ready..."
until docker exec slurmctld sinfo &>/dev/null; do
  sleep 2
done
echo "Cluster ready."
popd
