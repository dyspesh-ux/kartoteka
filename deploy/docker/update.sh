#!/usr/bin/env bash
# Update of the registry in one go: new code, new image, backup, restart, migrate.
#
#   ./update.sh                  # tag = today's date, mode = COMPOSE_MODE from registry.env (else http)
#   ./update.sh 2026-10-06       # own tag
#   ./update.sh 2026-10-06 own-cert
#
# Rollback (printed at the end as well): put the previous CUSTOM_TAG back into registry.env,
# ./compose.sh <mode> && ./dc.sh up -d; if migrate already changed the database, restore the backup
# made by this script.
set -euo pipefail

main() {
	cd "$(dirname "$0")"
	local env_file=registry.env tag mode old_tag
	[ -f "$env_file" ] || { echo "$env_file not found: cp registry.env.example $env_file" >&2; exit 1; }
	tag="${1:-$(date +%F)}"
	mode="${2:-$(sed -n 's/^COMPOSE_MODE=//p' "$env_file")}"
	mode="${mode:-http}"
	old_tag="$(sed -n 's/^CUSTOM_TAG=//p' "$env_file")"

	step "Код: git pull"
	git -C ../.. pull --ff-only

	step "Образ access-registry:$tag"
	./build.sh "$tag"

	step "Резервная копия (на работающем образе ${old_tag:-?})"
	if [ -f compose.registry.generated.yaml ] && [ -n "$(./dc.sh ps --status running -q backend 2>/dev/null)" ]; then
		./dc.sh bench --site all backup
	else
		echo "стек не запущен — копию пропускаю"
	fi

	step "Тег $tag в $env_file, режим $mode"
	if grep -q '^CUSTOM_TAG=' "$env_file"; then
		sed -i "s/^CUSTOM_TAG=.*/CUSTOM_TAG=$tag/" "$env_file"
	else
		echo "CUSTOM_TAG=$tag" >>"$env_file"
	fi
	./compose.sh "$mode"

	step "Перезапуск"
	./dc.sh up -d

	step "Миграция"
	./dc.sh bench --site all migrate

	echo
	echo "Готово: access-registry:$tag. Было: ${old_tag:-?}."
	echo "Откат: sed -i 's/^CUSTOM_TAG=.*/CUSTOM_TAG=$old_tag/' $env_file && ./compose.sh $mode && ./dc.sh up -d"
}

step() { printf '\n==> %s\n' "$*"; }

# the whole script is read before it runs: git pull may change this very file
main "$@"
exit
