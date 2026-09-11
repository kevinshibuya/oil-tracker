"""The commit every derived row is stamped with."""

import subprocess
from pathlib import Path

from oiltracker.paths import REPO_ROOT


def code_version(repo_root: Path = REPO_ROOT) -> str:
    """`git rev-parse --short HEAD`, so a derived row names the code that made it."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
