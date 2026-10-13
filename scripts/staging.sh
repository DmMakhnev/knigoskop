#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test -f .env.staging || { echo 'Создайте .env.staging по infra/staging.env.example' >&2; exit 1; }
case "${1:-}" in
  config) docker compose --env-file .env.staging -f infra/compose.staging.yaml config --quiet ;;
  build) docker compose --env-file .env.staging -f infra/compose.staging.yaml build api proxy migrate worker ;;
  start)
    docker compose --env-file .env.staging -f infra/compose.staging.yaml config --quiet
    docker compose --env-file .env.staging -f infra/compose.staging.yaml up -d postgres redis
    docker compose --env-file .env.staging -f infra/compose.staging.yaml run --rm migrate
    docker compose --env-file .env.staging -f infra/compose.staging.yaml up -d --no-build api worker proxy ;;
  status) docker compose --env-file .env.staging -f infra/compose.staging.yaml ps ;;
  stop) docker compose --env-file .env.staging -f infra/compose.staging.yaml stop ;;
  *) echo 'Usage: bash scripts/staging.sh config|build|start|status|stop' >&2; exit 2 ;;
esac
