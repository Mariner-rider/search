#!/usr/bin/env bash
set -euo pipefail

clone_or_update() {
  local repo_url="$1"
  local target_dir="$2"

  if [ -d "$target_dir/.git" ]; then
    echo "[update] $target_dir"
    git -C "$target_dir" fetch --all --tags
    default_branch=$(git -C "$target_dir" remote show origin | awk '/HEAD branch/ {print $NF}')
    git -C "$target_dir" checkout "$default_branch"
    git -C "$target_dir" pull --ff-only origin "$default_branch"
  else
    echo "[clone] $repo_url -> $target_dir"
    git clone --depth 1 "$repo_url" "$target_dir"
  fi
}

mkdir -p services frontend external

clone_or_update https://github.com/apache/nutch.git services/nutch
clone_or_update https://github.com/DigitalPebble/storm-crawler.git services/stormcrawler
clone_or_update https://github.com/yacy/yacy_search_server.git services/yacy
clone_or_update https://github.com/searxng/searxng.git frontend/searxng

clone_or_update https://github.com/deepset-ai/haystack.git external/haystack
clone_or_update https://github.com/qdrant/qdrant.git external/qdrant

echo "Done. Official repositories are available under ./services, ./frontend and ./external"
