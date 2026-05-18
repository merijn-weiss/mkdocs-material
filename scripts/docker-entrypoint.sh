#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo "🚀 Mondrian Docs Container"
echo "📂 Working directory: $(pwd)"
echo "=================================================="

#
# Optional included repos processing
#

if [[ "${ENABLE_INCLUDED_REPOS:-false}" == "true" ]]; then

    INCLUDED_FILE="${INCLUDED_REPOS_FILE:-included_repos.txt}"

    echo "📥 Included repos enabled"
    echo "📄 Using file: ${INCLUDED_FILE}"

    get-included-repos "${INCLUDED_FILE}"

else
    echo "ℹ️ Included repos disabled"
fi

#
# If arguments are passed, execute them directly.
# GitLab CI injects commands this way.
#

if [[ $# -gt 0 ]]; then
    echo "▶ Executing command: $*"
    exec "$@"
fi

#
# Default runtime behavior
#

echo "▶ Starting MkDocs development server"

exec mkdocs serve --dev-addr=0.0.0.0:8000