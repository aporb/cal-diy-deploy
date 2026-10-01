#!/usr/bin/env bash
# Promote cal.diy on porb-dev to a hardened image tag, or roll back to the baseline.
#
#   ./deploy/promote.sh v6.2.0-harbor2     # promote
#   ./deploy/promote.sh v6.2.0 --rollback  # roll back to the untouched baseline
#
# Why a script rather than a sed one-liner: this edits a compose file for a live
# client-facing service, so it backs the file up, refuses to run if the image is not already
# on the host (a failed pull mid-switch is how you get avoidable downtime), and verifies the
# result instead of assuming it.
#
# Downtime is the container recreate, a few seconds. The database is untouched: migrations
# are additive and already applied by the running version.
set -euo pipefail

DEPLOY_DIR="${CALDIY_DEPLOY_DIR:-/opt/cal-diy}"
COMPOSE="${DEPLOY_DIR}/docker-compose.yml"
SERVICE="calcom"
CONTAINER="cal-diy"
BASELINE="ghcr.io/aporb/cal-diy:v6.2.0"

TAG="${1:-}"
ROLLBACK="${2:-}"
if [ -z "$TAG" ]; then
  echo "usage: $0 <image-tag> [--rollback]"
  echo "  e.g. $0 v6.2.0-harbor2"
  echo "       $0 v6.2.0 --rollback"
  exit 2
fi

IMAGE="ghcr.io/aporb/cal-diy:${TAG}"

[ -f "$COMPOSE" ] || { echo "compose file not found: $COMPOSE"; exit 1; }

CURRENT_IMAGE="$(docker inspect "$CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || echo none)"
echo "currently running : ${CURRENT_IMAGE}"
echo "target            : ${IMAGE}"
[ -n "$ROLLBACK" ] && echo "mode              : ROLLBACK to baseline"
echo

if [ "$CURRENT_IMAGE" = "$IMAGE" ]; then
  echo "already on ${IMAGE} -- nothing to do."
  exit 0
fi

# The image must already be local. Pulling here would mean the pull happens while the old
# container is still serving, which is fine, but doing it separately makes the failure mode
# obvious rather than surfacing mid-recreate.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "image not present locally: ${IMAGE}"
  echo "pull it first, while the current container keeps serving:"
  echo "    docker pull ${IMAGE}"
  exit 1
fi

if [ -n "$ROLLBACK" ]; then
  TARGET_TAG="$BASELINE"
  docker image inspect "$TARGET_TAG" >/dev/null 2>&1 || {
    echo "baseline image not present locally: ${TARGET_TAG}"
    echo "pull it first:  docker pull ${TARGET_TAG}"
    exit 1
  }
  TARGET_TAG="${BASELINE}"
else
  TARGET_TAG="$IMAGE"
fi

echo "backing up compose file..."
BACKUP="${COMPOSE}.bak-$(date -u +%Y%m%dT%H%M%SZ)"
cp -p "$COMPOSE" "$BACKUP"
echo "  ${BACKUP}"

echo "rewriting the cal.diy image tag..."
sed -i "s|image: ghcr.io/aporb/cal-diy:.*|image: ${TARGET_TAG}|" "$COMPOSE"
grep -n "image: ghcr.io/aporb/cal-diy:" "$COMPOSE"

echo
echo "recreating the container..."
(cd "$DEPLOY_DIR" && docker compose up -d "$SERVICE")

echo
echo "waiting for health..."
for i in $(seq 1 30); do
  status="$(docker inspect "$CONTAINER" --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)"
  printf "  t+%02ds  health=%s\n" $((i * 5)) "$status"
  [ "$status" = "healthy" ] && break
  sleep 5
done

echo
echo "=== verification ==="
echo "running image : $(docker inspect "$CONTAINER" --format '{{.Config.Image}}')"
echo -n "local site    : "
curl -s -o /dev/null -w "HTTP %{http_code}\n" -m 20 "http://127.0.0.1:3005/amynporb" || true
echo -n "public site   : "
curl -s -o /dev/null -w "HTTP %{http_code}\n" -m 25 "https://book.harborgovcon.com/amynporb" || true
echo -n "page title    : "
curl -s -m 25 "https://book.harborgovcon.com/amynporb" | grep -oE '<title>[^<]*</title>' | head -1 || echo "(not found)"
echo -n "favicon       : "
curl -s -m 20 "https://book.harborgovcon.com/api/logo?type=favicon-32" -o /tmp/fav-check.png -w "HTTP %{http_code}, %{size_download} bytes\n" || true

echo
echo "If the title still says Cal.com, the container did not pick up the new image."
echo "Roll back with:  $0 ${TAG} --rollback"
