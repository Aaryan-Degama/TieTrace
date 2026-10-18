#!/usr/bin/env bash
# TieTrace local install (Linux or macOS; on Windows use WSL or Git Bash). Safe to run again: each step skips
# what is already done. Run it yourself from the repo root: ./install.sh
# Env (all optional): FALKORDB_PORT (6379), FALKORDB_BROWSER_PORT (3001), FALKORDB_CONTAINER (falkordb),
# TIETRACE_NEWSROOM (alpha). It never re-downloads from OpenAlex and never prints the .env key.
set -eu  # no pipefail: 'grep -q' closing a pipe early would turn a match into a failure
cd "$(dirname "$0")"

PORT="${FALKORDB_PORT:-6379}"
BROWSER_PORT="${FALKORDB_BROWSER_PORT:-3001}"
CONTAINER="${FALKORDB_CONTAINER:-falkordb}"
NEWSROOM="${TIETRACE_NEWSROOM:-alpha}"

step() { printf '\n== %s\n' "$1"; }
need() { command -v "$1" >/dev/null || { echo "Missing '$1'. Install it first: $2"; exit 1; }; }

step "1/6 Checking tools"
need docker "https://docs.docker.com/get-docker/"
need uv "https://docs.astral.sh/uv/getting-started/installation/"
docker info >/dev/null 2>&1 || { echo "Docker is installed but not running. Start Docker and run this again."; exit 1; }
echo "docker, uv: ok"

step "2/6 FalkorDB (container '$CONTAINER', port $PORT, Browser on $BROWSER_PORT)"
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  docker start "$CONTAINER" >/dev/null
  echo "existing container started"
else
  # Pinned v4.22.1 (never :latest), AOF on, ports bound to this machine only (see README).
  docker run -d --name "$CONTAINER" \
    -p "127.0.0.1:$PORT:6379" -p "127.0.0.1:$BROWSER_PORT:3000" \
    -v falkordb-data:/var/lib/falkordb/data \
    -e REDIS_ARGS="--appendonly yes --appendfsync everysec" --stop-timeout 120 \
    falkordb/falkordb:v4.22.1 >/dev/null
  echo "new container created"
fi
for _ in $(seq 1 30); do
  docker exec "$CONTAINER" redis-cli PING 2>/dev/null | grep -q PONG && break
  sleep 1
done
docker exec "$CONTAINER" redis-cli PING | grep -q PONG || { echo "FalkorDB did not answer within 30 s"; exit 1; }
[ "$(docker exec "$CONTAINER" redis-cli CONFIG GET appendonly | tail -1)" = yes ] \
  || echo "WARNING: AOF is off in this container, so a hard stop can lose data (README, step 2)."
echo "FalkorDB answers"

step "3/6 Python packages"
uv sync

step "4/6 Settings file"
if [ -f .env ]; then
  echo ".env already exists (left as it is)"
else
  cp .env.example .env
  echo "created .env from .env.example (add OPENALEX_API_KEY only if you build without the data zips)"
fi

step "5/6 Research graphs"
# case slug, raw cache folder, zip that holds that folder
build_case() {
  local slug="$1" raw="$2" zip="$3"
  if docker exec "$CONTAINER" redis-cli GRAPH.LIST | grep -qx "research:$slug"; then
    echo "$slug: graph already loaded"
    return
  fi
  if [ -z "$(ls -A "$raw" 2>/dev/null)" ] && [ -f "$zip" ]; then
    need unzip "your package manager (apt install unzip / brew install unzip)"
    unzip -oq "$zip"
    echo "$slug: unpacked $zip"
  fi
  if [ -z "$(ls -A "$raw" 2>/dev/null)" ]; then
    echo "$slug: SKIPPED, no cache in $raw and no $zip."
    echo "   Put $zip in data/ and run this again, or build from the OpenAlex API (uses your key,"
    echo "   up to max_api_calls in cases/$slug/case.json): uv run python loader.py --case $slug --rebuild"
    return
  fi
  echo "$slug: building from the cache (0 API calls, a few minutes)"
  FALKORDB_PORT="$PORT" uv run python loader.py --case "$slug" --rebuild
}
build_case obesity-coke data/raw data/tietrace-data.zip
build_case philip-morris-lung cases/philip-morris-lung/raw data/tietrace-pmi-raw.zip

step "6/6 Claude Code MCP server 'tietrace-$NEWSROOM'"
if ! command -v claude >/dev/null; then
  echo "Claude Code not found; skipped. Install it (https://claude.com/claude-code), then run this again."
elif claude mcp get "tietrace-$NEWSROOM" >/dev/null 2>&1; then
  echo "already registered"
else
  claude mcp add "tietrace-$NEWSROOM" -e TIETRACE_NEWSROOM="$NEWSROOM" -e FALKORDB_PORT="$PORT" \
    -- uv run --directory "$PWD" python server.py
fi

cat <<EOF

Done. Next:
  - Claude Code: run 'claude' in this folder, then ask for example "Which research cases are loaded?"
  - Web app:     FALKORDB_PORT=$PORT uv run uvicorn web.app:app --host 127.0.0.1 --port 8000
                 then open http://127.0.0.1:8000
  - FalkorDB Browser: http://127.0.0.1:$BROWSER_PORT
  - Before shutting down: docker exec $CONTAINER redis-cli SAVE && docker stop -t 120 $CONTAINER
EOF
