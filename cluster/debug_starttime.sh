#!/usr/bin/env bash
# Diagnose scontrol update StartTime behaviour in the multicats partition.
# Run after cluster is up and plugin is installed (test_e2e.sh sets that up).
set -eou pipefail

echo "=== Submitting 2 jobs to multicats partition ==="
J1=$(docker exec slurmctld sbatch --partition=multicats --time=0:30:00 --ntasks=1 --parsable --wrap "sleep 1800" | cut -d';' -f1)
J2=$(docker exec slurmctld sbatch --partition=multicats --time=0:30:00 --ntasks=1 --parsable --wrap "sleep 1800" | cut -d';' -f1)
echo "Job IDs: $J1 $J2"
sleep 1

echo ""
echo "=== Initial scontrol show job $J1 ==="
docker exec slurmctld scontrol show job "$J1"

echo ""
echo "=== Updating StartTime ==="
docker exec slurmctld scontrol update "JobId=$J1" StartTime=2026-08-15T10:05:00
docker exec slurmctld scontrol update "JobId=$J2" StartTime=2026-08-15T10:03:00

echo ""
echo "=== After update: scontrol show job $J1 ==="
docker exec slurmctld scontrol show job "$J1"

echo ""
echo "=== After update: scontrol show job $J2 ==="
docker exec slurmctld scontrol show job "$J2"

echo ""
echo "=== squeue --json output (start_time field) ==="
docker exec slurmctld squeue --json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for j in data.get('jobs', []):
    print(f\"job {j['job_id']}: start_time={j.get('start_time')} eligible_time={j.get('eligible_time')}\")
"

echo ""
echo "=== Cleaning up ==="
docker exec slurmctld scancel "$J1" "$J2"
