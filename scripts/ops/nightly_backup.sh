#!/usr/bin/env bash
set -euo pipefail
cd /opt/knigoskop
umask 077
mkdir -p /var/backups/knigoskop
exec 9>/var/backups/knigoskop/.backup.lock
flock -n 9 || { echo 'Another backup is running' >&2; exit 1; }
compose=(docker compose --env-file .env.staging -f infra/compose.staging.yaml -p knigoskop-staging)
# Explicit maintenance window. Restore only services that were running beforehand.
running=()
for service in api worker; do
  if [ -n "$("${compose[@]}" ps --status running -q "$service")" ]; then running+=("$service"); fi
done
resume() { if [ "${#running[@]}" -gt 0 ]; then "${compose[@]}" start "${running[@]}"; fi; }
trap resume EXIT
"${compose[@]}" stop api worker
python3 scripts/ops/backup_restore.py backup --project knigoskop-staging \
  --output "/var/backups/knigoskop/$(date -u +%Y%m%dT%H%M%SZ)"
