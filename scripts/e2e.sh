#!/usr/bin/env bash
# End-to-end test, run by CI: a real broker and a real Flink cluster.
#   1. a match replayed at high speed must come out identical to the reference;
#   2. a second match, with the task manager killed in the middle and restarted, must too:
#      the job recovers from its last checkpoint without losing or double-counting anything.
set -euo pipefail
cd "$(dirname "$0")/.."

wait_for() { for _ in $(seq 1 60); do eval "$1" >/dev/null 2>&1 && return 0; sleep 2; done; echo "timed out: $1"; exit 1; }

docker compose up -d redpanda jobmanager taskmanager
wait_for "docker compose exec -T redpanda rpk cluster health | grep -q 'Healthy:.*true'"
wait_for "curl -sf localhost:8081/taskmanagers | grep -q '\"slotsNumber\":2'"
docker compose exec -T redpanda rpk topic create match-events match-state match-late -p 1 || true
docker compose --profile submit run --rm submit
wait_for "curl -sf localhost:8081/jobs/overview | grep -q RUNNING"

echo "== 1. Nigeria-Cameroon, chaotic feed, as fast as possible"
python -m matchreplay.replay --match 3922238 --speed 0
python -m matchreplay.verify --match 3922238 --idle 60

echo "== 2. Senegal-Cote d'Ivoire (extra time, penalties), task manager killed mid-match"
python -m matchreplay.replay --match 3922242 --speed 300 &
REPLAY=$!
sleep 12
docker compose kill taskmanager
sleep 5
docker compose start taskmanager
wait $REPLAY
python -m matchreplay.verify --match 3922242 --idle 120
curl -s localhost:8081/jobs/overview | python -c "import json,sys; j=json.load(sys.stdin)['jobs'][0]; print('job', j['state'])"
