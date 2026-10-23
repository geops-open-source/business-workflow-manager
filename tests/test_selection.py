"""Tests for business_workflow_manager.selection (plain-text selection files)."""

from io import StringIO
from pathlib import Path

import pytest

from business_workflow_manager import WorkflowException, resolve_workflow_files
from business_workflow_manager.selection import read


def test_read_text_list():
    f = StringIO("# comment\na.yml\n\n  sub/b.yml  \n")
    assert read(f) == ["a.yml", "sub/b.yml"]


def test_resolve(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.yml").write_text("")
    (tmp_path / "a.yml").write_text("")
    assert resolve_workflow_files(["sub/b.yml", "a.yml"], tmp_path) == [
        tmp_path / "sub" / "b.yml",
        tmp_path / "a.yml",
    ]


def test_resolve_missing_listed_file(tmp_path: Path):
    with pytest.raises(WorkflowException, match="not found"):
        resolve_workflow_files(["missing.yml"], tmp_path)


def test_resolve_absolute_path(tmp_path: Path):
    with pytest.raises(WorkflowException, match="Absolute"):
        resolve_workflow_files([str(tmp_path / "a.yml")], tmp_path)
