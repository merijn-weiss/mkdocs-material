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
# GitLab CI shell execution
#

if [[ "${1:-}" == "sh" ]] || \
   [[ "${1:-}" == "/bin/sh" ]] || \
   [[ "${1:-}" == "bash" ]] || \
   [[ "${1:-}" == "/bin/bash" ]]; then

    echo "▶ Executing shell command: $*"
    exec "$@"
fi

#
# MkDocs CLI passthrough
#

if [[ $# -gt 0 ]]; then
    echo "▶ Executing MkDocs command: mkdocs $*"
    exec mkdocs "$@"
fi

#
# Default runtime behavior
#

echo "▶ Starting MkDocs development server"

exec mkdocs serve --dev-addr=0.0.0.0:8000