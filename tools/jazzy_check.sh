#!/usr/bin/env bash
# Builds and runs generated example nodes on ROS 2 Jazzy inside a throwaway Docker container, using the same script as CI
# (tests/jazzy/run.sh), so a pass here means the same commands pass there. By default only the examples marked `smoke` in
# tests/jazzy/examples.txt run; CI runs all of them.
#
#   tools/jazzy_check.sh [--all | <example> ...] [--with-tests] [--memory 6g] [--cpus 2] [--image ros:jazzy-ros-base] [--drop-image]
#
# What stays on the machine afterwards: the ROS image (it is needed next time; --drop-image removes it) and ./jazzy-check.log, which
# the next run overwrites. Everything else is removed after every run, even if it fails or you press Ctrl-C: the container, the
# copy of the project and the build workspace (they live only inside the container), and any container left behind by a crash.
# To remove the image and the log as well, run tools/jazzy_cleanup.sh.
set -uo pipefail

MEMORY=6g
CPUS=2
IMAGE=ros:jazzy-ros-base
DROP_IMAGE=0
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --memory) MEMORY="$2"; shift 2 ;;
    --cpus) CPUS="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --drop-image) DROP_IMAGE=1; shift ;;
    --all|--smoke|--with-tests) ARGS+=("$1"); shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) ARGS+=("$(basename "$1" .toml)"); shift ;;  # accepts `02_filter_pipeline` or `examples/02_filter_pipeline.toml`
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME=nodesmith-jazzy-check
LOG="$PWD/jazzy-check.log"

command -v docker >/dev/null || { echo "docker is not installed" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "cannot talk to the Docker daemon (is it running, and are you in the docker group?)" >&2; exit 1; }

remove_container() { docker ps -a --format '{{.Names}}' | grep -qx "$NAME" && docker rm -f "$NAME" >/dev/null 2>&1; true; }
cleanup() {
  remove_container
  if [ "$DROP_IMAGE" = 1 ] && docker image inspect "$IMAGE" >/dev/null 2>&1; then
    if [ -z "$(docker ps -a --filter "ancestor=$IMAGE" --format '{{.Names}}')" ]; then docker image rm "$IMAGE" >/dev/null 2>&1 && echo "removed image $IMAGE"
    else echo "image $IMAGE is used by another container and was left alone"; fi
  fi
}
trap cleanup EXIT
trap 'echo "interrupted"; exit 130' INT TERM

remove_container          # a container left behind by an earlier crash
echo "image: $IMAGE   limits: $MEMORY memory, $CPUS cpus   args: ${ARGS[*]:-(smoke set)}   log: $LOG"
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE" || exit 1

# Default bridge network, no published ports, no host networking: nothing here can clash with other containers on the machine.
# The project is mounted read-only and copied inside, so the host folder is never written to.
docker run --rm --name "$NAME" --memory "$MEMORY" --cpus "$CPUS" -e JOBS="$CPUS" \
  -v "$ROOT":/src:ro \
  "$IMAGE" bash -euo pipefail -c '
    mkdir /work
    tar -C /src --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache --exclude=ws -cf - . | tar -C /work -xf -
    exec bash /work/tests/jazzy/run.sh "$@"
  ' _ "${ARGS[@]+"${ARGS[@]}"}" 2>&1 | tee "$LOG"
STATUS="${PIPESTATUS[0]}"

if [ "$STATUS" -eq 0 ]; then echo "PASSED"; else echo "FAILED (exit $STATUS); see $LOG"; fi
exit "$STATUS"
