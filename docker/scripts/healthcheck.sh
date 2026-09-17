#!/usr/bin/env bash
set -euo pipefail
status_code=0
status=$(supervisorctl status) || status_code=$?
[[ "$status_code" = 0 || "$status_code" = 3 ]]
test "$(printf '%s\n' "$status" | wc -l)" -ge 20
if printf '%s\n' "$status" | grep -Ev '^vpn-[a-z][a-z0-9-]{0,11}[[:space:]]+STOPPED[[:space:]]' | grep -qv ' RUNNING '; then
    exit 1
fi
mysql -Nse 'SELECT 1' >/dev/null
psql -X -U postgres -d postgres -Atc 'SELECT 1' >/dev/null
test "$(redis-cli ping)" = PONG
if [[ -f /run/lamp-supervisor/cloudflared-lamp.conf ]]; then
    curl --noproxy '*' --fail --silent --max-time 2 http://127.0.0.1:20246/ready >/dev/null
fi
