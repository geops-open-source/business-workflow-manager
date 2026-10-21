"""Select workflow files for import via a plain-text selection file.

A selection file lists workflow files relative to a base directory (the
workflows folder), one per line::

    # comment
    auskunft.yml
    baugesuch/vorabklaerung.yml

Blank lines and lines starting with ``#`` are ignored.
"""

from pathlib import Path

from .exceptions import WorkflowSelectionError


def read(path: str | Path) -> list[str]:
    """Read the relative workflow file paths listed in ``path``.

    Raises:
        WorkflowSelectionError: if the file is missing or unreadable.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise WorkflowSelectionError(
            f"Cannot read workflow selection {str(path)!r}: {e}"
        ) from e
    entries: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


def resolve_workflow_files(entries: list[str], base_dir: str | Path) -> list[Path]:
    """Return the paths of ``entries`` relative to ``base_dir``.

    Raises:
        WorkflowSelectionError: if an entry is absolute or the file does not exist.
    """
    base = Path(base_dir)
    paths: list[Path] = []
    for entry in entries:
        if Path(entry).is_absolute():
            raise WorkflowSelectionError(f"Absolute path not allowed: {entry!r}")
        path = base / entry
        if not path.is_file():
            raise WorkflowSelectionError(f"Workflow file not found: {str(path)!r}")
        paths.append(path)
    return paths
