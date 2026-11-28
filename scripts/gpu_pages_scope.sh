#!/usr/bin/env bash
# Compare the GPU portion of this artifact with the last deployed GPU portion.
set -euo pipefail
paths=(data/gpu-rent docs/gpu-rent.html docs/gpu-rent.js docs/data/gpu-rent.csv
       docs/data/gpu-rent.json docs/data/gpu-rent-series docs/data/gpu-contracts.json scripts/build_gpu_rent.py
       scripts/gpu_rent_sources.py scripts/gpu_rent_archive.py scripts/gpu-rent-requirements.txt scripts/build_nebius.py
       scripts/gpu_pages_scope.sh tests/test_gpu_rent.py)
revision=$(git ls-tree -r HEAD -- "${paths[@]}" | git hash-object --stdin)
marker=gpu-rent-revision.txt
previous=$(mktemp)
trap 'rm -f "$previous"' EXIT
status=$(curl -sS -L --proto '=https' --proto-redir '=https' --retry 2 --max-time 15 \
    -o "$previous" -w '%{http_code}' "${PAGES_URL%/}/$marker?run=$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT")
case "$status" in
    200) if [[ $(cat "$previous") == "$revision" ]]; then changed=false; else changed=true; fi ;;
    404) changed=true ;; # First publication of the GPU page or its revision marker.
    *) echo "Cannot determine deployed GPU revision (HTTP $status)" >&2; exit 1 ;;
esac
printf 'changed=%s\n' "$changed" >> "$GITHUB_OUTPUT"
# This file is included only in the artifact that actually passes the publication gate.
printf '%s\n' "$revision" > "docs/$marker"
