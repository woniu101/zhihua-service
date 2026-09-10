#!/usr/bin/env bash
set -euo pipefail

repo_dir="${1:-/root/zhihua-service}"
source_file="$repo_dir/deploy/supervisor/zhihua-service.conf"
target_file="/etc/supervisor/conf.d/zhihua-service.conf"

if ! command -v supervisorctl >/dev/null 2>&1; then
  echo "supervisorctl is required by this image" >&2
  exit 1
fi
if [[ ! -f "$repo_dir/.env" || ! -x "$repo_dir/.venv/bin/python" ]]; then
  echo "zhihua-service environment is incomplete in $repo_dir" >&2
  exit 1
fi

install -m 0644 "$source_file" "$target_file"
supervisorctl reread
supervisorctl update
supervisorctl status zhihua-service
