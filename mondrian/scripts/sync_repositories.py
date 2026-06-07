#!/usr/bin/env python3

"""
Mondrian Docs
Included Repository Processor

Synchronizes external documentation repositories into:

    docs/included_repositories/

Repositories are cached locally and updated on subsequent runs.

Layout:

docs/
└── included_repositories/
    ├── .cache/
    │   └── namespace__repository/
    │       ├── .git
    │       └── docs/
    └── applications/
        └── nextcloud -> .cache/.../docs

This preserves Git history for MkDocs plugins such as:

    mkdocs-git-revision-date-localized-plugin

Supported providers:
  - gitlab
  - github
"""

import os
import shutil
import subprocess

from pathlib import Path


DEFAULT_WORKSPACE = (
    "docs/included_repositories"
)


DEFAULT_CACHE = (
    ".cache"
)


def sync_included_repositories(config):
    """
    Synchronize repositories defined in the manifest.
    """

    repositories = config.get(
        "included_repositories",
        []
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

    for repo in repositories:

        sync_repository(
            repo,
            workspace,
            cache_dir
        )

    print(
        "✓ Repository synchronization complete"
    )


def sync_repository(
    repo,
    workspace,
    cache_dir
):
    """
    Synchronize a single repository.
    """

    title = repo["title"]

    provider = repo["provider"]
    namespace = repo["namespace"]
    repository = repo["repository"]

    token_env = repo["token_env"]

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
        repository
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

        return

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
    """

    try:

        if checkout_dir.exists():

            print(
                "    ↻ Updating repository"
            )

            subprocess.run(
                [
                    "git",
                    "-C",
                    str(checkout_dir),
                    "fetch",
                    "--all",
                    "--prune"
                ],
                check=True
            )

            subprocess.run(
                [
                    "git",
                    "-C",
                    str(checkout_dir),
                    "checkout",
                    branch
                ],
                check=True
            )

            subprocess.run(
                [
                    "git",
                    "-C",
                    str(checkout_dir),
                    "pull",
                    "--ff-only"
                ],
                check=True
            )

        else:

            checkout_dir.parent.mkdir(
                parents=True,
                exist_ok=True
            )

            subprocess.run(
                [
                    "git",
                    "clone",
                    "--branch",
                    branch,
                    clone_url,
                    str(checkout_dir)
                ],
                check=True
            )

    except subprocess.CalledProcessError as exc:

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
