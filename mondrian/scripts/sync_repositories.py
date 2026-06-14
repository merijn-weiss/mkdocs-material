#!/usr/bin/env python3

"""
Mondrian Docs
Included Repository Processor

Synchronizes external documentation repositories into:

    docs/included_repositories/

Repositories are cached locally and updated on subsequent runs.

This preserves Git history for MkDocs plugins such as:

    mkdocs-git-revision-date-localized-plugin
    mkdocs-git-authors-plugin

Supported providers:
  - gitlab
  - github
"""

import os
import shutil
import subprocess
import yaml

from pathlib import Path


DEFAULT_WORKSPACE = (
    "docs/included_repositories"
)

DEFAULT_CACHE = (
    ".cache"
)

MANIFEST_FILE = (
    "docs.manifest.yml"
)


def load_yaml(path):

    p = Path(path)

    if not p.exists():
        return {}

    with open(p, "r") as f:
        return yaml.safe_load(f) or {}


def merge_repository_defaults(included_repositories):

    defaults = included_repositories.get(
        "defaults",
        {}
    )

    repositories = included_repositories.get(
        "repositories",
        []
    )

    result = []

    for repo in repositories:

        merged = dict(defaults)
        merged.update(repo)

        result.append(
            merged
        )

    return result


def derive_root_target_path(repo):

    namespace_leaf = (
        repo["namespace"]
        .split("/")[-1]
    )

    return str(
        Path(namespace_leaf) /
        repo["repository"]
    )


def derive_child_target_path(
    parent_target_path,
    child_repo
):

    namespace_leaf = (
        child_repo["namespace"]
        .split("/")[-1]
    )

    return str(
        Path(parent_target_path) /
        "included_repositories" /
        namespace_leaf /
        child_repo["repository"]
    )


def run_git(cmd):

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            result.stderr.strip()
        )


def sync_included_repositories(config):
    """
    Synchronize repositories defined in the manifest.
    Supports recursive included repositories.
    """

    included = config.get(
        "included_repositories",
        {}
    )

    repositories = merge_repository_defaults(
        included
    )

    if not repositories:
        return

    workspace = Path(
        DEFAULT_WORKSPACE
    )

    workspace.mkdir(
        parents=True,
        exist_ok=True
    )

    cache_dir = (
        workspace /
        DEFAULT_CACHE
    )

    cache_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    print(
        "▶ Synchronizing included repositories"
    )

    visited = set()

    sync_repository_list(
        repositories,
        workspace,
        cache_dir,
        visited
    )

    print(
        "✓ Repository synchronization complete"
    )


def sync_repository_list(
    repositories,
    workspace,
    cache_dir,
    visited
):

    for repo in repositories:

        checkout_dir = sync_repository(
            repo,
            workspace,
            cache_dir,
            visited
        )

        if not checkout_dir:
            continue

        sync_child_repositories(
            repo,
            checkout_dir,
            workspace,
            cache_dir,
            visited
        )


def sync_repository(
    repo,
    workspace,
    cache_dir,
    visited
):
    """
    Synchronize a single repository.
    """

    title = repo.get(
        "title",
        repo["repository"]
    )

    provider = repo["provider"]
    namespace = repo["namespace"]
    repository = repo["repository"]
    token_env = repo["token_env"]

    repo_key = (
        provider,
        namespace,
        repository
    )

    if repo_key in visited:

        print(
            f"  ↺ [{title}] Already synchronized, skipping"
        )

        return None

    visited.add(
        repo_key
    )

    branch = repo.get(
        "branch",
        "main"
    )

    source_path = repo.get(
        "source_path",
        "docs"
    )

    target_path = repo.get(
        "target_path",
        derive_root_target_path(
            repo
        )
    )

    token = os.environ.get(
        token_env
    )

    if not token:

        print(
            f"⚠ [{title}] "
            f"Environment variable "
            f"'{token_env}' not set"
        )

        return None

    clone_url = build_clone_url(
        provider,
        namespace,
        repository,
        token
    )

    namespace_dir = (
        namespace.replace(
            "/",
            "__"
        )
    )

    checkout_dir = (
        cache_dir /
        namespace_dir /
        repository
    )

    target_dir = (
        workspace /
        target_path
    )

    print(
        f"  ▶ [{title}]"
    )

    clone_or_update_repository(
        clone_url,
        checkout_dir,
        branch,
        title
    )

    create_docs_link(
        checkout_dir,
        source_path,
        target_dir,
        title
    )

    repo["target_path"] = target_path

    return checkout_dir


def sync_child_repositories(
    parent_repo,
    parent_checkout_dir,
    workspace,
    cache_dir,
    visited
):
    """
    Read child included repositories from a synced repository manifest.
    """

    manifest_file = (
        parent_checkout_dir /
        MANIFEST_FILE
    )

    if not manifest_file.exists():
        return

    child_manifest = load_yaml(
        manifest_file
    )

    included = child_manifest.get(
        "included_repositories",
        {}
    )

    if not included:
        return

    repositories = merge_repository_defaults(
        included
    )

    if not repositories:
        return

    parent_target_path = parent_repo.get(
        "target_path",
        derive_root_target_path(
            parent_repo
        )
    )

    for repo in repositories:

        repo["target_path"] = derive_child_target_path(
            parent_target_path,
            repo
        )

    sync_repository_list(
        repositories,
        workspace,
        cache_dir,
        visited
    )


def build_clone_url(
    provider,
    namespace,
    repository,
    token
):
    """
    Build authenticated clone URL.
    """

    if provider == "gitlab":

        return (
            f"https://gitlab-access-token:{token}"
            f"@gitlab.com/"
            f"{namespace}/"
            f"{repository}.git"
        )

    if provider == "github":

        return (
            f"https://{token}"
            f"@github.com/"
            f"{namespace}/"
            f"{repository}.git"
        )

    raise ValueError(
        f"Unsupported provider: {provider}"
    )


def clone_or_update_repository(
    clone_url,
    checkout_dir,
    branch,
    title
):
    """
    Clone or update repository.

    Uses full Git history for MkDocs Git plugins.
    """

    try:

        if checkout_dir.exists():

            print(
                "    ↻ Updating repository"
            )

            run_git(
                [
                    "git",
                    "-C",
                    str(checkout_dir),
                    "fetch",
                    "origin",
                    "--prune"
                ]
            )

            run_git(
                [
                    "git",
                    "-C",
                    str(checkout_dir),
                    "checkout",
                    branch
                ]
            )

            run_git(
                [
                    "git",
                    "-C",
                    str(checkout_dir),
                    "reset",
                    "--hard",
                    f"origin/{branch}"
                ]
            )

        else:

            checkout_dir.parent.mkdir(
                parents=True,
                exist_ok=True
            )

            print(
                "    ↓ Cloning repository"
            )

            run_git(
                [
                    "git",
                    "clone",
                    "--branch",
                    branch,
                    clone_url,
                    str(checkout_dir)
                ]
            )

    except Exception as exc:

        raise RuntimeError(
            f"[{title}] Failed to synchronize repository"
        ) from exc


def create_docs_link(
    checkout_dir,
    source_path,
    target_dir,
    title
):
    """
    Create a symlink to the documentation directory.
    """

    source_dir = (
        checkout_dir /
        source_path
    )

    if not source_dir.exists():

        print(
            f"    ⚠ Source path not found: "
            f"{source_path}"
        )

        return

    if target_dir.is_symlink():

        target_dir.unlink()

    elif target_dir.exists():

        shutil.rmtree(
            target_dir
        )

    target_dir.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    target_dir.symlink_to(
        source_dir.resolve(),
        target_is_directory=True
    )

    print(
        f"    ✓ "
        f"{title} → {target_dir}"
    )


if __name__ == "__main__":

    print(
        "This module is intended to be imported "
        "and called from render-config.py"
    )