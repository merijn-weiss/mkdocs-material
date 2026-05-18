#!/usr/bin/env bash
set -euo pipefail

INPUT_FILE="${1:-included_repos.txt}"

echo "📥 Getting documentation from included repositories"

mkdir -p ./included_repos

if [[ ! -f "${INPUT_FILE}" ]]; then
    echo "ℹ️ File '${INPUT_FILE}' not found"
    exit 0
fi

# Ensure file ends with newline
if [[ -n "$(tail -c 1 "${INPUT_FILE}" || true)" ]]; then
    echo >> "${INPUT_FILE}"
fi

while IFS= read -r line || [[ -n "$line" ]]; do

    # Skip empty lines/comments
    [[ -z "$line" ]] && continue
    [[ "$line" =~ ^# ]] && continue

    echo "──────────────────────────────────────────────"
    echo "🔍 Processing: $line"

    REP_ACCESS_TOKEN=$(
        echo "$line" |
        awk '{for(i=1;i<=NF;i++) if($i ~ /^REP_ACCESS_TOKEN=/) print $i}' |
        cut -d= -f2
    )

    REPO_PROJECT_NAMESPACE=$(
        echo "$line" |
        awk '{for(i=1;i<=NF;i++) if($i ~ /^REPO_PROJECT_NAMESPACE=/) print $i}' |
        cut -d= -f2
    )

    REPO=$(
        echo "$line" |
        awk '{for(i=1;i<=NF;i++) if($i ~ /^REPO=/) print $i}' |
        cut -d= -f2
    )

    if [[ -z "${REP_ACCESS_TOKEN}" ]]; then
        echo "⚠️ REP_ACCESS_TOKEN missing"
        continue
    fi

    RESOLVED_TOKEN="$(printenv "${REP_ACCESS_TOKEN}" || true)"

    if [[ -z "${RESOLVED_TOKEN}" ]]; then
        echo "⚠️ Environment variable '${REP_ACCESS_TOKEN}' not set"
        continue
    fi

    TARGET_DIR="./included_repos/$(basename "${REPO}" .git)"

    echo "📂 Target directory: ${TARGET_DIR}"

    if [[ -d "${TARGET_DIR}/.git" ]]; then
        echo "🔄 Updating existing repository"

        git -C "${TARGET_DIR}" pull --ff-only
    else
        echo "📥 Cloning repository"

        git clone \
            "https://gitlab-access-token:${RESOLVED_TOKEN}@${REPO_PROJECT_NAMESPACE}${REPO}" \
            "${TARGET_DIR}"
    fi

done < "${INPUT_FILE}"

echo "✅ Finished processing repositories"