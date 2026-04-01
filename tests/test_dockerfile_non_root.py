"""
Tests that the Dockerfile switches to a non-root user before CMD.

Running a container as root raises the attack surface. A dedicated
non-root USER directive must appear after all RUN/COPY/ADD steps that
need root privileges.
"""

import re
from pathlib import Path


DOCKERFILE = Path(__file__).parent.parent / "Dockerfile"


def read_lines():
    with open(DOCKERFILE) as fh:
        return [line.rstrip() for line in fh]


def last_user_value(lines):
    value = None
    for line in lines:
        m = re.match(r"^\s*USER\s+(\S+)", line, re.IGNORECASE)
        if m:
            value = m.group(1)
    return value


class TestDockerfileNonRoot:
    def test_has_non_root_user_directive(self):
        lines = read_lines()
        user = last_user_value(lines)
        assert user is not None, (
            "Dockerfile has no USER directive. Add a non-root USER before CMD."
        )
        assert user.lower() != "root" and user != "0", (
            f"Last USER directive is {user!r}. Use a dedicated non-root user."
        )

    def test_user_comes_after_install_steps(self):
        lines = read_lines()
        last_user_idx = None
        last_privileged_idx = None
        for i, line in enumerate(lines):
            if re.match(r"^\s*USER\s+", line, re.IGNORECASE):
                last_user_idx = i
            if re.match(r"^\s*(RUN|COPY|ADD)\s+", line, re.IGNORECASE):
                last_privileged_idx = i

        assert last_user_idx is not None, "Dockerfile has no USER directive."
        assert last_privileged_idx is not None, "Dockerfile has no RUN/COPY/ADD."
        assert last_user_idx > last_privileged_idx, (
            f"USER (line {last_user_idx + 1}) appears before the last "
            f"RUN/COPY/ADD (line {last_privileged_idx + 1}). "
            "Move USER after all install and copy steps."
        )
