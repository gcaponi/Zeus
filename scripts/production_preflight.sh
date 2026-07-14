#!/usr/bin/env bash
set -euo pipefail

app_dir="${ZEUS_APP_DIR:-/opt/Zeus}"
cd "$app_dir"

printf 'app_dir=%s\n' "$app_dir"
printf 'head=%s\n' "$(git rev-parse --short HEAD)"
printf 'branch=%s\n' "$(git branch --show-current || true)"

tracked_status="$(git status --porcelain --untracked-files=no)"
if [[ -n "$tracked_status" ]]; then
    printf 'tracked_worktree=dirty\n' >&2
    printf '%s\n' "$tracked_status" >&2
    exit 1
fi
printf 'tracked_worktree=clean\n'

printf 'untracked_files:\n'
git status --short --untracked-files=all | sed -n 's/^?? /  - /p'

for service in zeus zeus-celery nginx; do
    state="$(systemctl is-active "$service")"
    printf 'service_%s=%s\n' "$service" "$state"
    [[ "$state" == "active" ]]
done

[[ -f .env ]]
for variable in \
    DJANGO_SECRET_KEY \
    POSTGRES_DB \
    POSTGRES_USER \
    POSTGRES_PASSWORD \
    POSTGRES_HOST \
    REDIS_URL; do
    if ! grep -q "^${variable}=" .env; then
        printf 'missing_env=%s\n' "$variable" >&2
        exit 1
    fi
done
printf 'required_env=present\n'

if grep -Eiq '^ZEUS_APP_SHELL_ENABLED=(1|true|yes)$' .env; then
    printf 'app_shell_flag=enabled\n'
elif grep -q '^ZEUS_APP_SHELL_ENABLED=' .env; then
    printf 'app_shell_flag=disabled\n'
else
    printf 'app_shell_flag=missing-default-disabled\n'
fi

local_health="$(curl -fsS -H 'Host: zeus.cais.uno' http://127.0.0.1:8000/health/)"
public_health="$(curl -fsS https://zeus.cais.uno/health/)"
expected_health='{"status": "ok"}'
if [[ "$local_health" != "$expected_health" ]]; then
    printf 'invalid_local_health=%s\n' "$local_health" >&2
    exit 1
fi
if [[ "$public_health" != "$expected_health" ]]; then
    printf 'invalid_public_health=%s\n' "$public_health" >&2
    exit 1
fi
printf 'local_health=%s\n' "$local_health"
printf 'public_health=%s\n' "$public_health"

df -h "$app_dir" | tail -n 1
