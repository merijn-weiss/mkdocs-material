#!/usr/bin/env python3

from pathlib import Path

import yaml

from jinja2 import Environment
from jinja2 import FileSystemLoader

from sync_repositories import (
    sync_included_repositories
)

#
# Load local .env if present
#

from dotenv import load_dotenv

load_dotenv(
    ".env",
    override=False
)

MONDRIAN_HOME = "/opt/mondrian"

DEFAULTS_FILE = (
    f"{MONDRIAN_HOME}/defaults/docs.manifest.yml"
)

TEMPLATE_DIR = (
    f"{MONDRIAN_HOME}/templates"
)

MANIFEST_FILE = "docs.manifest.yml"

OVERRIDE_FILE = "mkdocs.override.yml"

OUTPUT_FILE = ".mkdocs.generated.yml"


def load_yaml(path):

    p = Path(path)

    if not p.exists():
        return {}

    with open(p, "r") as f:
        return yaml.safe_load(f) or {}


def deep_merge(base, override):

    result = dict(base)

    for key, value in override.items():

        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(
                result[key],
                value
            )
        else:
            result[key] = value

    return result


def apply_defaults(config):

    #
    # Feature Flags
    #

    features = config.setdefault(
        "features",
        {}
    )

    features.setdefault(
        "macros",
        False
    )

    features.setdefault(
        "tags",
        False
    )

    features.setdefault(
        "print_page",
        False
    )

    #
    # Included Repositories
    #

    config.setdefault(
        "included_repositories",
        {}
    )

    config["included_repositories"].setdefault(
        "defaults",
        {}
    )

    config["included_repositories"].setdefault(
        "repositories",
        []
    )

    #
    # Repository Settings
    #

    repository = config.setdefault(
        "repository",
        {}
    )

    if repository.get("url"):

        repository.setdefault(
            "branch",
            "main"
        )

        repository.setdefault(
            "docs_dir",
            "docs"
        )

        repository["edit_uri"] = (
            f"edit/"
            f"{repository['branch']}/"
            f"{repository['docs_dir']}"
        )

def apply_repository_defaults(config):

    included = config.get(
        "included_repositories",
        {}
    )

    defaults = included.get(
        "defaults",
        {}
    )

    repositories = included.get(
        "repositories",
        []
    )

    for repo in repositories:

        merged = dict(defaults)

        merged.update(repo)

        repo.clear()
        repo.update(merged)

def normalize_repository_configuration(config):
    """
    Normalize included repository configuration.
    """

    included = config.get(
        "included_repositories",
        {}
    )

    included.setdefault(
        "defaults",
        {}
    )

    included.setdefault(
        "repositories",
        []
    )

def manage_generated_content(config):

    features = config["features"]

    #
    # Tags Page
    #

    tags_file = Path(
        "docs/tags.md"
    )

    if features["tags"]:

        tags_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        tags_file.write_text(
            "# Tags\n\n"
            "<!-- material/tags -->\n",
            encoding="utf-8"
        )

    elif tags_file.exists():

        tags_file.unlink()


#
# Load Configuration
#

defaults = load_yaml(
    DEFAULTS_FILE
)

manifest = load_yaml(
    MANIFEST_FILE
)

override = load_yaml(
    OVERRIDE_FILE
)

config = deep_merge(
    defaults,
    manifest
)

config = deep_merge(
    config,
    override
)

#
# Apply Defaults
#

apply_defaults(
    config
)

#
# Apply Repository Defaults
#

apply_repository_defaults(
    config
)

normalize_repository_configuration(
    config
)

#
# Generate Content
#

manage_generated_content(
    config
)

#
# Sync Included Repositories
#

sync_included_repositories(
    config
)

#
# Render Template
#

env = Environment(
    loader=FileSystemLoader(
        TEMPLATE_DIR
    ),
    trim_blocks=True,
    lstrip_blocks=True
)

template = env.get_template(
    "mkdocs.yml.j2"
)

rendered = template.render(
    **config
)

output_file = Path(
    OUTPUT_FILE
)

output_file.write_text(
    rendered,
    encoding="utf-8"
)
