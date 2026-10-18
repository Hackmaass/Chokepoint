#!/bin/sh
# One container for a small public host: FalkorDB in the background, the example slice loaded once, then the API.
set -e
export FALKORDB_HOST=127.0.0.1 FALKORDB_PORT=6379
export FALKORDB_PASSWORD="${FALKORDB_PASSWORD:-$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
redis-server --requirepass "$FALKORDB_PASSWORD" --save "" --appendonly no --bind 127.0.0.1 --dir /tmp \
  --loadmodule /opt/falkordb/falkordb.so MAX_QUEUED_QUERIES 50 TIMEOUT_MAX 60000 TIMEOUT_DEFAULT 3000 RESULTSET_SIZE 1000000 &
for _ in $(seq 1 60); do REDISCLI_AUTH="$FALKORDB_PASSWORD" redis-cli -h 127.0.0.1 ping 2>/dev/null | grep -q PONG && break; sleep 1; done
python scripts/load_ibm.py --csv examples/slice/HI-Small_slice.csv
exec uvicorn chokepoint.api:app --host 0.0.0.0 --port "${PORT:-7860}" --workers 1 --no-access-log
