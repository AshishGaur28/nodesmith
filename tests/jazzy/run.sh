#!/usr/bin/env bash
# Generates example nodes, builds them with colcon on ROS 2 Jazzy and runs tests/jazzy/e2e.py against them. THE one place that
# defines this sequence: CI (.github/workflows/ci.yml) and tools/jazzy_check.sh both call it, so they cannot drift apart.
# Run it inside a ROS 2 Jazzy environment (the ros:jazzy-ros-base image) as root; it installs its tools with apt.
#
#   tests/jazzy/run.sh [--smoke | --all | <example> ...] [--with-tests] [--lint]
#
# --smoke (default) builds the examples marked `smoke` in tests/jazzy/examples.txt, --all every example.
# --lint also runs the ROS 2 linters (`colcon test`) on the generated packages and prints what they find. It is informational:
# it does not change the result.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

SCOPE=smoke
WITH_TESTS=0
LINT=0
NAMES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --smoke) SCOPE=smoke; shift ;;
    --all) SCOPE=all; shift ;;
    --with-tests) WITH_TESTS=1; shift ;;
    --lint) LINT=1; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) NAMES+=("$1"); shift ;;
  esac
done

TABLE=tests/jazzy/examples.txt
SELECTED=()      # example:package:extras
while read -r example package scope extras run; do
  case "$example" in ''|'#'*) continue ;; esac
  if [ ${#NAMES[@]} -gt 0 ]; then
    for n in "${NAMES[@]}"; do
      if [ "$n" = "$example" ]; then SELECTED+=("$example:$package:$extras"); fi
    done
  elif [ "$SCOPE" = all ] || [ "$scope" = smoke ]; then
    SELECTED+=("$example:$package:$extras")
  fi
done < "$TABLE"
[ ${#SELECTED[@]} -gt 0 ] || { echo "no example selected" >&2; exit 2; }
echo "examples: $(printf '%s ' "${SELECTED[@]%%:*}")"

echo "== tools"
if ! command -v colcon >/dev/null || ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3-venv python3-pip python3-colcon-common-extensions build-essential > /dev/null
fi

if [ "$LINT" = 1 ]; then
  echo "== lint tools"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ros-jazzy-ament-lint-auto ros-jazzy-ament-lint-common > /dev/null
fi

echo "== install nodesmith"
[ -x /opt/nodesmith-venv/bin/python ] || python3 -m venv /opt/nodesmith-venv
/opt/nodesmith-venv/bin/pip install -q -e ".[dev]"
if [ "$WITH_TESTS" = 1 ]; then echo "== pytest"; /opt/nodesmith-venv/bin/pytest -q; fi

echo "== generate"
rm -rf ws && mkdir -p ws/src
for item in "${SELECTED[@]}"; do
  IFS=: read -r example package extras <<< "$item"
  # A node with user functions takes them from a logic folder; the check uses a real implementation (tests/jazzy/user/<package>/).
  logic=()
  if [ -d "tests/jazzy/user/$package" ]; then logic=(--logic-dir "tests/jazzy/user/$package"); fi
  /opt/nodesmith-venv/bin/nodesmith generate --manifest "examples/$example.toml" --target-language cpp --output-dir "ws/src/$package" "${logic[@]}"
  for extra in ${extras//,/ }; do
    if [ "$extra" != - ] && [ ! -d "ws/src/$extra" ]; then cp -r "tests/jazzy/$extra" ws/src/; fi
  done
done

echo "== colcon build"
# ROS's setup scripts read variables they never set, so `set -u` is off while they are sourced.
set +u
# shellcheck disable=SC1091
. /opt/ros/jazzy/setup.sh
set -u
(cd ws && colcon build --event-handlers console_direct+ ${JOBS:+--parallel-workers "$JOBS"})

echo "== run the nodes and check their behaviour"
set +u
# shellcheck disable=SC1091
. ws/install/setup.sh
set -u
status=0
python3 tests/jazzy/e2e.py "${SELECTED[@]%%:*}" || status=$?

if [ "$LINT" = 1 ]; then
  echo "== lint (informational: it does not change the result)"
  (cd ws && colcon test --event-handlers console_direct+ && colcon test-result --verbose) || true
fi
exit "$status"
