#!/usr/bin/env bash
set -euo pipefail

: "${SERVER_IMAGE:?必须设置 SERVER_IMAGE}"
: "${WEB_IMAGE:?必须设置 WEB_IMAGE}"
: "${EXPECTED_REVISION:?必须设置 EXPECTED_REVISION}"
: "${EXPECTED_VERSION:?必须设置 EXPECTED_VERSION}"

read_label() {
  docker image inspect --format "{{ index .Config.Labels \"$2\" }}" "$1"
}

verify_labels() {
  local image="$1"
  local source revision version base_name
  source="$(read_label "$image" org.opencontainers.image.source)"
  revision="$(read_label "$image" org.opencontainers.image.revision)"
  version="$(read_label "$image" org.opencontainers.image.version)"
  base_name="$(read_label "$image" org.opencontainers.image.base.name)"

  test -n "$source"
  test "$revision" = "$EXPECTED_REVISION"
  test "$version" = "$EXPECTED_VERSION"
  test -n "$base_name"
}

verify_labels "$SERVER_IMAGE"
verify_labels "$WEB_IMAGE"

docker run --rm -i --read-only --network none \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint python "$SERVER_IMAGE" - <<'PY'
from importlib.metadata import version
from inspect import signature

assert version("powermem") == "0.5.3"

from powermem import AsyncMemory, UserMemory

for owner, method_names in (
    (AsyncMemory, ("add", "search")),
    (UserMemory, ("add", "search", "profile")),
):
    for method_name in method_names:
        parameters = signature(getattr(owner, method_name)).parameters
        assert "user_id" in parameters, f"{owner.__name__}.{method_name} 缺少 user_id"

import core.family_identity
import core.api.family_memory_handler
import tools.family_memory.cli
import tools.family_memory.preflight
PY

docker run --rm --read-only --network none \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --entrypoint sh "$SERVER_IMAGE" -ec '
    root=/opt/xiaozhi-esp32-server
    test ! -e "$root/data"
    test ! -e "$root/tests"
    test ! -e "$root/.test_tmp"
    ! find "$root" \( -name __pycache__ -o -name "*.pyc" -o -name "*.pyo" -o -name "*.db" -o -name "*.sqlite" -o -name "*.sqlite3" -o -name "*.wal" -o -name "*.shm" \) -print -quit | grep -q .
  '

docker run --rm --read-only --network none \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --entrypoint sh "$WEB_IMAGE" -ec '
    java -version
    test -f /app/xiaozhi-esp32-api.jar
    test -f /usr/share/nginx/html/index.html
    grep -R -q "/family-memory" /usr/share/nginx/html
    ! grep -R -F -q "server.secret" /usr/share/nginx/html
    ! grep -R -E -q "ghp_[A-Za-z0-9]{20,}|github_pat_|BEGIN [A-Z ]*PRIVATE KEY" /usr/share/nginx/html
    ! find /app /usr/share/nginx/html \( -name ".env" -o -name "*.key" -o -name "*.pem" \) -print -quit | grep -q .
  '

echo "家庭记忆镜像只读验证通过"
