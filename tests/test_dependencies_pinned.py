"""
Tests that all packages in pyproject.toml are pinned to exact versions.

Unpinned dependencies make deployments non-reproducible: a fresh
pip install may pull incompatible versions and break the application.
Every dependency must use == to pin an exact version.
"""

import re
import tomllib
from pathlib import Path


PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def load_all_deps():
    with open(PYPROJECT, "rb") as fh:
        data = tomllib.load(fh)
    project = data.get("project", {})
    deps = project.get("dependencies", [])
    dev_deps = []
    for group in project.get("optional-dependencies", {}).values():
        dev_deps.extend(group)
    return deps, dev_deps


class TestDependenciesPinned:
    def test_main_deps_pinned_with_exact_version(self):
        deps, _ = load_all_deps()
        unpinned = [d for d in deps if not re.search(r"==", d)]
        assert not unpinned, (
            "The following packages in [project.dependencies] are not pinned with ==:\n"
            + "\n".join(unpinned)
        )

    def test_dev_deps_pinned_with_exact_version(self):
        _, dev_deps = load_all_deps()
        unpinned = [d for d in dev_deps if not re.search(r"==", d)]
        assert not unpinned, (
            "The following packages in [project.optional-dependencies] are not pinned with ==:\n"
            + "\n".join(unpinned)
        )
