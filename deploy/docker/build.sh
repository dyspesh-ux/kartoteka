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

# Preflight: the build clones Frappe and installs Python/Node packages inside Docker's storage.
# When that storage is full or sits on a file system overlay2 does not support, git and pip fail
# with odd errors (e.g. «unable to create directory for .git/logs/HEAD»). Check it before building.
# SKIP_PREFLIGHT=1 skips the checks; MIN_FREE_GB sets the free space needed (default 15).
preflight() {
	local root driver fs dtype free_kb min_gb virt problems=0
	root="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null)" || { echo "docker info failed: is Docker running and may this user use it?" >&2; exit 1; }
	driver="$(docker info -f '{{.Driver}}')"
	fs="$(docker info -f '{{range .DriverStatus}}{{if eq (index . 0) "Backing Filesystem"}}{{index . 1}}{{end}}{{end}}')"
	dtype="$(docker info -f '{{range .DriverStatus}}{{if eq (index . 0) "Supports d_type"}}{{index . 1}}{{end}}{{end}}')"
	# the containerd image store (Docker 24+ option, default in 29) does not report it: ask the kernel
	[ -n "$fs" ] || fs="$(stat -f -c %T "$root" 2>/dev/null || true)"
	min_gb="${MIN_FREE_GB:-15}"
	free_kb="$(df -Pk "$root" 2>/dev/null | awk 'NR==2 {print $4}')"
	echo "Docker: storage $driver on ${fs:-?} (d_type ${dtype:-?}), $root, free $(( ${free_kb:-0} / 1024 / 1024 )) GB"
	if [ -n "$free_kb" ] && [ "$free_kb" -lt $(( min_gb * 1024 * 1024 )) ]; then
		echo "  ✗ less than ${min_gb} GB free for Docker. Free space: docker builder prune -af; docker image prune -a" >&2
		echo "    (old images of the registry: docker images access-registry) and check: df -h $root" >&2
		problems=1
	fi
	if [ "$dtype" = "false" ]; then
		echo "  ✗ the file system under $root has no d_type (XFS with ftype=0): overlay2 breaks." >&2
		echo "    Move Docker's data-root to ext4 or XFS with ftype=1 (/etc/docker/daemon.json → \"data-root\")." >&2
		problems=1
	fi
	case "$driver" in overlay2|overlayfs) ;; *)
		echo "  ! storage driver is $driver, frappe_docker is tested with overlay2" >&2 ;;
	esac
	case "$fs" in zfs|btrfs|nfs|nfs4|tmpfs|overlayfs|fuseblk|fuse*)
		echo "  ! overlay2 on $fs is known to fail on git and pip: use ext4/xfs for Docker's data-root" >&2 ;;
	esac
	virt="$(systemd-detect-virt -c 2>/dev/null || true)"
	if [ "$virt" = "lxc" ] || [ "$virt" = "lxc-libvirt" ]; then
		echo "  ! Docker runs inside an LXC container: overlay2 there needs nesting and keyctl, and often fails." >&2
		echo "    A virtual machine is the safer place for the stack." >&2
	fi
	if [ "$problems" -ne 0 ]; then
		echo "Fix the above or run with SKIP_PREFLIGHT=1 to build anyway." >&2
		exit 1
	fi
}
[ "${SKIP_PREFLIGHT:-0}" = "1" ] || preflight

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
