#!/usr/bin/env bash
# Test scontrol update StartTime with a time in the past.
set -eou pipefail

PAST=$(date -u -d '2 minutes ago' +%Y-%m-%dT%H:%M:%S)
FUTURE=$(date -u -d '5 minutes' +%Y-%m-%dT%H:%M:%S)
echo "Past time:   $PAST"
echo "Future time: $FUTURE"

J=$(docker exec slurmctld sbatch --partition=multicats --time=0:30:00 --ntasks=1 --parsable --wrap "sleep 1800" | cut -d';' -f1)
echo "Job ID: $J"
sleep 1

echo ""
echo "=== Setting StartTime to PAST ($PAST) ==="
docker exec slurmctld scontrol update "JobId=$J" "StartTime=$PAST" || echo "FAILED"
echo ""
echo "--- scontrol show job $J ---"
docker exec slurmctld scontrol show job "$J" | grep -E "StartTime|EligibleTime|Reason"

echo ""
echo "=== Setting StartTime to FUTURE ($FUTURE) ==="
docker exec slurmctld scontrol update "JobId=$J" "StartTime=$FUTURE" || echo "FAILED"
echo ""
echo "--- scontrol show job $J ---"
docker exec slurmctld scontrol show job "$J" | grep -E "StartTime|EligibleTime|Reason"

docker exec slurmctld scancel "$J"
