#!/usr/bin/env bash

set -euo pipefail

MONDRIAN_HOME="/opt/mondrian"

MKDOCS_HOST="${MKDOCS_HOST:-0.0.0.0}"
MKDOCS_PORT="${MKDOCS_PORT:-8000}"

echo "=================================================="
echo "🚀 Mondrian Docs Container"
echo "📂 Working directory: $(pwd)"
echo "=================================================="

ensure_gitignore() {

    local MONDRIAN_ENTRIES=(
        ".env"
        ".mkdocs.generated.yml"
        "docs/_baseSiteAssets/"
        "docs/included_repositories/"
    )

    if [[ ! -f .gitignore ]]; then

        {
            echo "# Mondrian Docs"

            for entry in "${MONDRIAN_ENTRIES[@]}"; do
                echo "${entry}"
            done

        } > .gitignore

        return
    fi

    local missing=false

    for entry in "${MONDRIAN_ENTRIES[@]}"; do

        if ! grep -qxF "${entry}" .gitignore; then
            missing=true
            break
        fi

    done

    if [[ "${missing}" == "true" ]]; then

        {
            echo ""
            echo "# Mondrian Docs"

            for entry in "${MONDRIAN_ENTRIES[@]}"; do

                if ! grep -qxF "${entry}" .gitignore; then
                    echo "${entry}"
                fi

            done

        } >> .gitignore

    fi
}

install_assets() {

    mkdir -p docs

    if [[ ! -d docs/_baseSiteAssets ]]; then

        echo "📦 Installing Mondrian assets"

        cp -r \
            "${MONDRIAN_HOME}/assets/_baseSiteAssets" \
            docs/

    fi

    ensure_gitignore
}

#
# Shell support
#

case "${1:-}" in

    sh|/bin/sh|bash|/bin/bash)

        echo "▶ Starting shell"
        exec "$@"

        ;;

esac

#
# Debug support
#

case "${1:-}" in

    exec)

        shift

        echo "▶ Executing: $*"
        exec "$@"

        ;;

esac

#
# Manifest mode
#

if [[ -f docs.manifest.yml ]]; then

    ensure_gitignore

    echo "▶ Using docs.manifest.yml"
    echo "▶ Running mondrian-docs $*"

    exec mondrian-docs "$@"

fi

#
# Legacy mode
#

if [[ -f mkdocs.yml ]]; then

    echo "📗 Detected legacy mkdocs.yml"

    install_assets

    if [[ $# -eq 0 ]]; then

        echo "▶ Starting MkDocs development server"
        echo "🌐 Listening on ${MKDOCS_HOST}:${MKDOCS_PORT}"

        exec mkdocs serve \
            --dev-addr="${MKDOCS_HOST}:${MKDOCS_PORT}"

    fi

    if [[ "${1:-}" == "serve" ]]; then

        shift

        echo "▶ Starting MkDocs development server"
        echo "🌐 Listening on ${MKDOCS_HOST}:${MKDOCS_PORT}"

        exec mkdocs serve \
            --dev-addr="${MKDOCS_HOST}:${MKDOCS_PORT}" \
            "$@"

    fi

    echo "▶ Running: mkdocs $*"

    exec mkdocs "$@"

fi

#
# Nothing found
#

echo "❌ No documentation configuration found"
echo ""
echo "Expected one of:"
echo "  - docs.manifest.yml"
echo "  - mkdocs.yml"

exit 1