"""Tests for business_workflow_manager.selection (plain-text selection files)."""

from pathlib import Path

import pytest

from business_workflow_manager import WorkflowSelectionError, resolve_workflow_files
from business_workflow_manager.selection import read


def test_read_text_list(tmp_path: Path):
    path = tmp_path / "workflows.txt"
    path.write_text("# comment\na.yml\n\n  sub/b.yml  \n")
    assert read(path) == ["a.yml", "sub/b.yml"]


def test_read_missing_file(tmp_path: Path):
    with pytest.raises(WorkflowSelectionError, match="Cannot read"):
        read(tmp_path / "missing.txt")


def test_resolve(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.yml").write_text("")
    (tmp_path / "a.yml").write_text("")
    assert resolve_workflow_files(["sub/b.yml", "a.yml"], tmp_path) == [
        tmp_path / "sub" / "b.yml",
        tmp_path / "a.yml",
    ]


def test_resolve_missing_listed_file(tmp_path: Path):
    with pytest.raises(WorkflowSelectionError, match="not found"):
        resolve_workflow_files(["missing.yml"], tmp_path)


def test_resolve_absolute_path(tmp_path: Path):
    with pytest.raises(WorkflowSelectionError, match="Absolute"):
        resolve_workflow_files([str(tmp_path / "a.yml")], tmp_path)
