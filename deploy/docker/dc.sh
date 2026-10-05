#!/usr/bin/env bash
# docker compose for the registry stack, from any directory: the stack file and the project name
# (registry) are always the right ones.
#
#   ./dc.sh ps
#   ./dc.sh logs -f backend queue-long scheduler
#   ./dc.sh bench --site all migrate          # = exec backend bench --site all migrate
#   ./dc.sh exec backend bash
#
# The stack file is made by ./compose.sh (own-cert, http or letsencrypt).
set -euo pipefail
cd "$(dirname "$0")"
FILE=compose.registry.generated.yaml
[ -f "$FILE" ] || {
	echo "$FILE not found in $(pwd): run ./compose.sh own-cert (or http, letsencrypt) first" >&2
	exit 1
}
compose=(docker compose --project-name "${PROJECT:-registry}" -f "$FILE")
if [ "${1:-}" = "bench" ]; then
	# no terminal (cron, scripts): exec without a TTY
	tty=()
	[ -t 0 ] || tty=(-T)
	exec "${compose[@]}" exec "${tty[@]}" backend "$@"
fi
exec "${compose[@]}" "$@"
