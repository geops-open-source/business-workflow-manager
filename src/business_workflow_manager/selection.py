"""Select workflow files for import via a plain-text selection file.

A selection file lists workflow files relative to a base directory (the
workflows folder), one per line::

    # comment
    auskunft.yml
    baugesuch/vorabklaerung.yml

Blank lines and lines starting with ``#`` are ignored.
"""

from pathlib import Path
from typing import TextIO

from .exceptions import WorkflowException


def read(f: TextIO) -> list[str]:
    """Read the relative workflow file paths listed in the stream ``f``."""
    entries: list[str] = []
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


def resolve_workflow_files(entries: list[str], base_dir: str | Path) -> list[Path]:
    """Return the paths of ``entries`` relative to ``base_dir``.

    Raises:
        WorkflowException: if an entry is absolute or the file does not exist.
    """
    base = Path(base_dir)
    paths: list[Path] = []
    for entry in entries:
        if Path(entry).is_absolute():
            raise WorkflowException(f"Absolute path not allowed: {entry!r}")
        path = base / entry
        if not path.is_file():
            raise WorkflowException(f"Workflow file not found: {str(path)!r}")
        paths.append(path)
    return paths
