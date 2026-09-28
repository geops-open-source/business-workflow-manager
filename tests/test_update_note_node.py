"""Tests for WorkflowManager.update_note_node."""

from datetime import datetime
from io import StringIO

import pytest
from sqlalchemy.orm import Session

from business_workflow_manager import NoteNodeInfo, WorkflowManager, WorkflowNotFound
from business_workflow_manager.types import NodeStatus

WORKFLOW_CONFIG = """\
workflow:
  title: Test workflow
  version: 1
  min_per_entity: 0
  max_per_entity: null
  start_task_ref: task1
  tasks:
    task1:
      title: Task 1
      steps: []
      links:
        - task_ref: END
    END:
      title: __END__
      steps: []
      links: []
"""


@pytest.fixture
def note_node(mgr: WorkflowManager) -> NoteNodeInfo:
    ts = datetime(2024, 6, 15, 10, 0, 0)
    return mgr.create_note_node(
        entity_id="entity-1",
        title="Original Title",
        started_at=ts,
        note="Original note",
        is_public=False,
    )


def test_update_title(mgr: WorkflowManager, note_node: NoteNodeInfo):
    result = mgr.update_note_node(note_node.wf_node_id, title="New Title")
    assert result.title == "New Title"


def test_update_started_at_syncs_timestamps(
    mgr: WorkflowManager, note_node: NoteNodeInfo
):
    """Updating started_at also sets finished_at and deadline to the same value."""
    new_ts = datetime(2025, 1, 1, 12, 0, 0)
    result = mgr.update_note_node(note_node.wf_node_id, started_at=new_ts)
    assert result.started_at == new_ts
    assert result.finished_at == new_ts
    assert result.deadline == new_ts


def test_update_note(mgr: WorkflowManager, note_node: NoteNodeInfo):
    result = mgr.update_note_node(note_node.wf_node_id, note="Updated note")
    assert result.note == "Updated note"


def test_clear_note(mgr: WorkflowManager, note_node: NoteNodeInfo):
    result = mgr.update_note_node(note_node.wf_node_id, note=None)
    assert result.note is None


def test_update_is_public(mgr: WorkflowManager, note_node: NoteNodeInfo):
    result = mgr.update_note_node(note_node.wf_node_id, is_public=True)
    assert result.is_public is True


def test_status_always_finished(mgr: WorkflowManager, note_node: NoteNodeInfo):
    result = mgr.update_note_node(note_node.wf_node_id, title="Changed")
    assert result.status == NodeStatus.FINISHED


def test_unchanged_fields_preserved(mgr: WorkflowManager, note_node: NoteNodeInfo):
    result = mgr.update_note_node(note_node.wf_node_id, title="New Title")
    assert result.note == "Original note"
    assert result.is_public is False
    assert result.started_at == datetime(2024, 6, 15, 10, 0, 0)


def test_multiple_fields(mgr: WorkflowManager, note_node: NoteNodeInfo):
    new_ts = datetime(2025, 3, 1)
    result = mgr.update_note_node(
        note_node.wf_node_id,
        title="Updated",
        started_at=new_ts,
        note="New note",
        is_public=True,
    )
    assert result.title == "Updated"
    assert result.started_at == new_ts
    assert result.finished_at == new_ts
    assert result.deadline == new_ts
    assert result.note == "New note"
    assert result.is_public is True


def test_returns_note_node_info(mgr: WorkflowManager, note_node: NoteNodeInfo):
    result = mgr.update_note_node(note_node.wf_node_id, title="X")
    assert isinstance(result, NoteNodeInfo)


def test_not_found(mgr: WorkflowManager):
    with pytest.raises(WorkflowNotFound):
        mgr.update_note_node(99999, title="X")


def test_wrong_node_type(mgr: WorkflowManager, session: Session):
    """Raises WorkflowNotFound if the node is not a NoteNode."""
    mgr2 = WorkflowManager(session)
    wf, _ = mgr2.load_workflow_from_file(StringIO(WORKFLOW_CONFIG))
    mgr2.session.flush()
    mgr2.session.expire_all()
    wf_node = mgr2.start_workflow(wf.wf_config_id, entity_id="entity-1")

    with pytest.raises(WorkflowNotFound, match="not a NoteNode"):
        mgr2.update_note_node(wf_node.wf_node_id, title="X")


def test_url_default_none(mgr: WorkflowManager, note_node: NoteNodeInfo):
    assert note_node.url is None


def test_url_set(mgr: WorkflowManager, note_node: NoteNodeInfo):
    result = mgr.update_note_node(note_node.wf_node_id, url="https://example.com/note")
    assert result.url == "https://example.com/note"


def test_clear_url(mgr: WorkflowManager, note_node: NoteNodeInfo):
    mgr.update_note_node(note_node.wf_node_id, url="https://example.com/note")
    result = mgr.update_note_node(note_node.wf_node_id, url=None)
    assert result.url is None


def test_url_unchanged_when_omitted(mgr: WorkflowManager, note_node: NoteNodeInfo):
    mgr.update_note_node(note_node.wf_node_id, url="https://example.com/note")
    result = mgr.update_note_node(note_node.wf_node_id, title="New Title")
    assert result.url == "https://example.com/note"
