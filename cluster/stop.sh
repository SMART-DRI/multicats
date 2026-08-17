#!/usr/bin/env bash
set -eou pipefail
pushd "$(dirname "$0")"
docker compose down
popd
