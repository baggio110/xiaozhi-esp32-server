#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE_COMPOSE="${BASE_COMPOSE:-$ROOT_DIR/main/xiaozhi-server/docker-compose_all.yml}"
OVERRIDE_COMPOSE="${OVERRIDE_COMPOSE:-$ROOT_DIR/deploy/family-memory/compose.family-memory.yml}"

: "${FAMILY_MEMORY_SERVER_IMAGE:?必须设置 FAMILY_MEMORY_SERVER_IMAGE}"
: "${FAMILY_MEMORY_WEB_IMAGE:?必须设置 FAMILY_MEMORY_WEB_IMAGE}"

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

docker compose --project-name family-memory-verify \
  -f "$BASE_COMPOSE" config --format json >"$work_dir/base.json"
docker compose --project-name family-memory-verify \
  -f "$BASE_COMPOSE" -f "$OVERRIDE_COMPOSE" \
  config --format json >"$work_dir/merged.json"

jq -e --arg image "$FAMILY_MEMORY_SERVER_IMAGE" \
  '.services["xiaozhi-esp32-server"].image == $image' \
  "$work_dir/merged.json" >/dev/null
jq -e --arg image "$FAMILY_MEMORY_WEB_IMAGE" \
  '.services["xiaozhi-esp32-server-web"].image == $image' \
  "$work_dir/merged.json" >/dev/null

jq -S 'del(
  .services["xiaozhi-esp32-server"].image,
  .services["xiaozhi-esp32-server-web"].image
)' "$work_dir/base.json" >"$work_dir/base.normalized.json"
jq -S 'del(
  .services["xiaozhi-esp32-server"].image,
  .services["xiaozhi-esp32-server-web"].image
)' "$work_dir/merged.json" >"$work_dir/merged.normalized.json"

cmp "$work_dir/base.normalized.json" "$work_dir/merged.normalized.json"
cp "$work_dir/merged.json" "${COMPOSE_REPORT:-$ROOT_DIR/family-memory-compose.json}"
echo "Compose覆盖验证通过：除两个定制镜像外，其余配置完全不变"
