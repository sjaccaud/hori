#!/bin/bash
# Re-apply Open-WebUI model workspace/prompt/context configuration after
# the container is recreated or the DB is reset.
#
# Usage (run from the repo root or anywhere):
#   USER=$USER HOME=$HOME OPENWEBUI_USER_NAME=<display-name> \
#     services/open-webui/apply-model-config.sh
#
# The script stops the container, copies out webui.db, runs the Python
# fix scripts, copies the DB back, removes stale -wal/-shm files, and
# restarts the container.

set -euo pipefail

: "${USER:?USER must be set}"
: "${HOME:?HOME must be set}"
OPENWEBUI_USER_NAME="${OPENWEBUI_USER_NAME:-$USER}"
OPENWEBUI_CONTAINER="${OPENWEBUI_CONTAINER:-open_webui_aios}"
DB_PATH="/app/backend/data/webui.db"

scripts_dir="$(cd "$(dirname "$0")" && pwd)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

echo "Stopping $OPENWEBUI_CONTAINER..."
docker stop "$OPENWEBUI_CONTAINER" >/dev/null

echo "Copying DB to tmp..."
docker cp "$OPENWEBUI_CONTAINER:$DB_PATH" "$tmp_dir/webui.db"

echo "Applying model config..."
OPENWEBUI_USER_NAME="$OPENWEBUI_USER_NAME" USER="$USER" HOME="$HOME" \
  python3 "$scripts_dir/fix_tooling.py" "$tmp_dir/webui.db"

python3 "$scripts_dir/set_context_lengths.py" "$tmp_dir/webui.db"

python3 "$scripts_dir/make_models_workspace.py" "$tmp_dir/webui.db"

echo "Copying DB back and clearing stale WAL/SHM files..."
docker cp "$tmp_dir/webui.db" "$OPENWEBUI_CONTAINER:$DB_PATH"
docker run --rm --volumes-from "$OPENWEBUI_CONTAINER" busybox rm -f \
  "$DB_PATH-shm" "$DB_PATH-wal" 2>/dev/null || true

echo "Starting $OPENWEBUI_CONTAINER..."
docker start "$OPENWEBUI_CONTAINER" >/dev/null

for i in $(seq 1 60); do
  if docker ps --format '{{.Status}}' --filter "name=$OPENWEBUI_CONTAINER" | grep -q 'healthy'; then
    echo "Container is healthy."
    break
  fi
  sleep 2
done

echo "Open-WebUI model config applied."
