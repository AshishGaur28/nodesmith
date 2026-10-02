#!/usr/bin/env bash
# Removes what tools/jazzy_check.sh created: its container (if one was left behind), the ROS image, and the log. Nothing else.
# It never runs `docker system prune` or `docker volume prune`: on a machine with other containers (for example Supabase) those can
# delete data that is not ours.
#
#   tools/jazzy_cleanup.sh [--image ros:jazzy-ros-base] [--yes]
#
# Without --yes it shows what it would remove and asks.
set -euo pipefail

IMAGE=ros:jazzy-ros-base
YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2 ;;
    --yes) YES=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

NAME=nodesmith-jazzy-check
LOG="$PWD/jazzy-check.log"
command -v docker >/dev/null || { echo "docker is not installed" >&2; exit 1; }

echo "This will remove:"
docker ps -a --format '{{.Names}}' | grep -qx "$NAME" && echo "  container  $NAME"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "  image      $IMAGE ($(docker image inspect "$IMAGE" --format '{{.Size}}' | awk '{printf "%.1f GB", $1 / 1e9}'))"
  # Stop if any other container still uses the image: it is not ours to remove then.
  USERS="$(docker ps -a --filter "ancestor=$IMAGE" --format '{{.Names}}' | grep -vx "$NAME" || true)"
  if [ -n "$USERS" ]; then echo "but these containers still use $IMAGE, so it is left alone:"; echo "$USERS" | sed 's/^/  /'; IMAGE=""; fi
else
  echo "  (image $IMAGE is not present)"
  IMAGE=""
fi
[ -f "$LOG" ] && echo "  file       $LOG"
echo "It does not touch any other image, container, volume or network."

if [ "$YES" -ne 1 ]; then
  read -r -p "Continue? [y/N] " answer
  [ "$answer" = y ] || [ "$answer" = Y ] || { echo "nothing removed"; exit 0; }
fi

docker ps -a --format '{{.Names}}' | grep -qx "$NAME" && docker rm -f "$NAME" >/dev/null && echo "removed container $NAME"
if [ -n "$IMAGE" ]; then docker image rm "$IMAGE" >/dev/null && echo "removed image $IMAGE"; fi
[ -f "$LOG" ] && rm -f "$LOG" && echo "removed $LOG"
echo "done"
