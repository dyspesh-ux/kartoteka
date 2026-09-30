#!/usr/bin/env bash
# Builds the Docker image of the registry the way frappe_docker recommends:
# images/layered/Containerfile + apps.json passed as a BuildKit secret (tokens never land in layers).
#
#   ./build.sh [tag]            # tag defaults to today's date, e.g. access-registry:2026-10-01
#
# Needs Docker Engine 23+ (BuildKit), git, and apps.json next to this script (see apps.json.example).
# Behind a company proxy: DOCKER_BUILD_ARGS="--network=host --build-arg HTTPS_PROXY=http://proxy:3128" ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

TAG="${1:-$(date +%F)}"
IMAGE="${CUSTOM_IMAGE:-access-registry}"
FRAPPE_BRANCH="${FRAPPE_BRANCH:-version-15}"          # the registry is built for Frappe v15
# frappe_docker commit the stack is tested with; update deliberately, not by accident
FRAPPE_DOCKER_REF="${FRAPPE_DOCKER_REF:-48a34e5f1620c2f71253d70af21da14c9c931ea8}"
FRAPPE_DOCKER_DIR="${FRAPPE_DOCKER_DIR:-.frappe_docker}"

[ -f apps.json ] || { echo "apps.json not found: cp apps.json.example apps.json (for a private repo put a token in the URL)" >&2; exit 1; }

if [ ! -d "$FRAPPE_DOCKER_DIR/.git" ]; then
	git clone --quiet https://github.com/frappe/frappe_docker "$FRAPPE_DOCKER_DIR"
fi
git -C "$FRAPPE_DOCKER_DIR" fetch --quiet origin "$FRAPPE_DOCKER_REF" 2>/dev/null || git -C "$FRAPPE_DOCKER_DIR" fetch --quiet origin
git -C "$FRAPPE_DOCKER_DIR" checkout --quiet "$FRAPPE_DOCKER_REF"

docker build \
	--build-arg=FRAPPE_PATH=https://github.com/frappe/frappe \
	--build-arg=FRAPPE_BRANCH="$FRAPPE_BRANCH" \
	--build-arg=CACHE_BUST="$(date +%s)" \
	${FRAPPE_IMAGE_PREFIX:+--build-arg=FRAPPE_IMAGE_PREFIX="$FRAPPE_IMAGE_PREFIX"} \
	${DOCKER_BUILD_ARGS:-} \
	--secret=id=apps_json,src=apps.json \
	--tag="$IMAGE:$TAG" \
	--file="$FRAPPE_DOCKER_DIR/images/layered/Containerfile" \
	"$FRAPPE_DOCKER_DIR"

echo
echo "Built $IMAGE:$TAG. Put CUSTOM_TAG=$TAG into registry.env and run ./compose.sh"
