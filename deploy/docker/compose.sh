#!/usr/bin/env bash
# Generates compose.registry.yaml from frappe_docker's compose.yaml and overrides
# (as frappe_docker recommends: `docker compose ... config > compose.custom.yaml`).
#
#   ./compose.sh http          # plain HTTP on HTTP_PUBLISH_PORT (behind a company reverse proxy)
#   ./compose.sh own-cert      # HTTPS with a company certificate (tls/registry.crt, tls/registry.key)
#   ./compose.sh letsencrypt   # HTTPS by Traefik + Let's Encrypt (public DNS, ports 80/443 open)
#
# Volumes and networks are named registry_* (PROJECT=registry).
# The database is external (DB_HOST in registry.env), so the MariaDB override is not used.
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-http}"
FD="${FRAPPE_DOCKER_DIR:-.frappe_docker}"
ENV_FILE="${ENV_FILE:-registry.env}"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE not found: cp registry.env.example $ENV_FILE" >&2; exit 1; }
[ -d "$FD" ] || { echo "$FD not found: run ./build.sh first" >&2; exit 1; }

files=(-f "$FD/compose.yaml" -f "$FD/overrides/compose.redis.yaml" -f "$FD/overrides/compose.backup-cron.yaml")
case "$MODE" in
	http) files+=(-f "$FD/overrides/compose.noproxy.yaml") ;;
	own-cert) files+=(-f compose.own-cert.yaml) ;;
	letsencrypt) files+=(-f "$FD/overrides/compose.https.yaml") ;;
	*) echo "unknown mode $MODE (http | own-cert | letsencrypt)" >&2; exit 1 ;;
esac

docker compose --project-name "${PROJECT:-registry}" --project-directory . --env-file "$ENV_FILE" "${files[@]}" config > compose.registry.generated.yaml
echo "compose.registry.generated.yaml is ready. Start: ./dc.sh up -d (then ./dc.sh ps, ./dc.sh bench ...)"
