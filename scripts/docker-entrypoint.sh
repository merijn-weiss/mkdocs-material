#!/usr/bin/env bash
set -euo pipefail

if [[ -f included_repos.txt ]]; then
    echo "Processing included repositories"
    get-included-repos
fi

#
# GitLab CI injects:
#   sh -c "..."
#
# If first arg looks like a shell, execute directly.
#

if [[ "${1:-}" == "sh" ]] || [[ "${1:-}" == "/bin/sh" ]]; then
    exec "$@"
fi

#
# Default runtime behavior
#

exec mkdocs "$@"