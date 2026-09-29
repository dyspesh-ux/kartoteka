#!/bin/bash
# Access Registry: read-only collector of shared folder permissions on Synology DSM.
#
# Reads the privileges of every shared folder (synoshare) and the Windows ACL of folders
# (synoacltool) and sends them to the registry. It does not change anything on the NAS.
#
# Which folders are recorded: the root of every shared folder always; a subfolder only when its
# rights differ from the rights of its parent. With STOP_ON_SAME=1 the collector does not go into
# a subfolder whose rights are the same as the parent's (fast); with STOP_ON_SAME=0 it walks the
# whole tree down to MAX_DEPTH and still records only the folders where rights change (complete).
#
# Install (DSM 7): Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script,
# user root, daily at night; script: bash /volume1/scripts/registry_collect.sh
# Test by hand over SSH:   sudo bash registry_collect.sh --dry-run   (writes the file, sends nothing)

REGISTRY_URL="https://registry.example.local"   # address of the Frappe site of the registry
API_KEY=""                                      # API key and secret of a registry user with role 1C Sync
API_SECRET=""
SERVER_CODE="NAS01"                             # code of this NAS in «Файловые серверы»
STOP_ON_SAME=1
MAX_DEPTH=12
SHARES=""                                       # shares to read, space separated; empty — all
SKIP_SHARES="homes home photo web_packages"      # never read these
OUT="/tmp/registry-acl.txt"
CURL_OPTS=""                                    # e.g. "--cacert /volume1/scripts/ca.pem"; -k only for tests

set -u
PATH="/usr/syno/sbin:/usr/syno/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

if [ "$(id -u)" != "0" ]; then
	echo "Run as root (DSM Task Scheduler: user root; SSH: sudo)" >&2
	exit 1
fi
command -v synoacltool >/dev/null || { echo "synoacltool not found: this is not DSM?" >&2; exit 1; }

: > "$OUT"
emit() { printf '%s\n' "$*" >> "$OUT"; }
b64() { printf '%s' "$1" | base64 | tr -d '\n'; }
acl_entries() { printf '%s\n' "$1" | grep -E '^[[:space:]]*\[[0-9]+\]'; }
# rights of a folder without the entry numbers and inheritance levels: equal strings = same rights
normalize() { acl_entries "$1" | sed -E 's/^[[:space:]]*\[[0-9]+\][[:space:]]*//; s/[[:space:]]*\(level:[0-9]+\)[[:space:]]*$//' | sort; }

walk() {
	local share="$1" dir="$2" rel="$3" depth="$4" parent_norm="$5"
	local acl norm same=0
	acl="$(synoacltool -get "$dir" 2>&1)"
	norm="$(normalize "$acl")"
	if [ "$depth" -gt 0 ] && [ "$norm" = "$parent_norm" ]; then
		same=1
	fi
	if [ "$same" -eq 0 ]; then
		emit "DIR $(b64 "$share") $(b64 "${rel:-/}") $depth"
		printf '%s\n' "$acl" | sed 's/^/ACL /' >> "$OUT"
	fi
	if [ "$same" -eq 1 ] && [ "$STOP_ON_SAME" = "1" ]; then
		return
	fi
	[ "$depth" -ge "$MAX_DEPTH" ] && return
	find "$dir" -mindepth 1 -maxdepth 1 -type d \
		! -name '@eaDir' ! -name '#recycle' ! -name '#snapshot' ! -name '@*' -print0 2>/dev/null |
		sort -z |
		while IFS= read -r -d '' child; do
			walk "$share" "$child" "$rel/${child##*/}" $((depth + 1)) "$norm"
		done
}

emit "#REGISTRY-SYNOLOGY 1"
emit "SERVER $(b64 "$SERVER_CODE")"
emit "HOST $(b64 "$(hostname)")"
emit "TIME $(date '+%Y-%m-%dT%H:%M:%S%z')"
emit "DSM $(b64 "$(grep -h productversion /etc.defaults/VERSION 2>/dev/null | cut -d'"' -f2)")"
emit "STOP_ON_SAME $STOP_ON_SAME"
emit "MAX_DEPTH $MAX_DEPTH"

if [ -z "$SHARES" ]; then
	SHARES="$(synoshare --enum ALL 2>/dev/null | sed '1,/Listed:/d')"
fi

printf '%s\n' "$SHARES" | tr ' ' '\n' | while IFS= read -r share; do
	[ -z "$share" ] && continue
	case " $SKIP_SHARES " in *" $share "*) continue ;; esac
	info="$(synoshare --get "$share" 2>&1)"
	path="$(printf '%s\n' "$info" | sed -n 's/^[[:space:]]*Path[ .]*\[\(.*\)\][[:space:]]*$/\1/p' | head -n 1)"
	emit "SHARE $(b64 "$share")"
	printf '%s\n' "$info" | sed 's/^/INFO /' >> "$OUT"
	if [ -n "$path" ] && [ -d "$path" ]; then
		walk "$share" "$path" "" 0 ""
	else
		emit "ERROR $(b64 "$share") $(b64 "path not found: $path")"
	fi
done
emit "END"

echo "Collected: $(grep -c '^DIR ' "$OUT") folders, $(grep -c '^SHARE ' "$OUT") shares → $OUT"
if [ "$DRY_RUN" = "1" ]; then
	exit 0
fi
gzip -c "$OUT" > "$OUT.gz"
# shellcheck disable=SC2086
curl -sS $CURL_OPTS -w '\nHTTP %{http_code}\n' -X POST \
	-H "Authorization: token $API_KEY:$API_SECRET" \
	-H "Content-Type: application/octet-stream" \
	--data-binary @"$OUT.gz" \
	"$REGISTRY_URL/api/method/access_registry.file_shares.api.upload?server=$SERVER_CODE"
echo
