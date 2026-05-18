#!/usr/bin/env bash
set -euo pipefail

if [[ -f included_repos.txt ]]; then
    echo "Processing included repositories"
    get-included-repos
fi

exec mkdocs "$@"