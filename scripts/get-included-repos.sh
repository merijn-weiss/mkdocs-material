#!/usr/bin/env bash
set -euo pipefail

echo "Getting documentation from included repositories"

mkdir -p ./included_repos

if [[ ! -f included_repos.txt ]]; then
    echo "ℹ️ No included_repos.txt found"
    exit 0
fi

# Ensure newline at EOF
if [[ -n "$(tail -c 1 included_repos.txt || true)" ]]; then
    echo >> included_repos.txt
fi

while IFS= read -r line || [[ -n "$line" ]]; do

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

    RESOLVED_TOKEN=$(printenv "$REP_ACCESS_TOKEN" || true)

    if [[ -z "$RESOLVED_TOKEN" ]]; then
        echo "⚠️ Token variable '$REP_ACCESS_TOKEN' not set"
        continue
    fi

    TARGET_DIR="./included_repos/$(basename "${REPO}" .git)"

    if [[ -d "$TARGET_DIR/.git" ]]; then
        echo "📂 Updating existing repo"
        git -C "$TARGET_DIR" pull --ff-only
    else
        echo "Cloning repo"
        git clone \
            "https://gitlab-access-token:${RESOLVED_TOKEN}@${REPO_PROJECT_NAMESPACE}${REPO}" \
            "$TARGET_DIR"
    fi

done < included_repos.txt

echo "✅ Finished processing repositories"