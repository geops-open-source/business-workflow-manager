"""Tests for WorkflowManager.create_note_node."""

from datetime import datetime
from io import StringIO

import pytest

from business_workflow_manager import (
    NoteNodeInfo,
    WorkflowManager,
    WorkflowNodeInfo,
    WorkflowNotFound,
)
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
def wf_node(mgr: WorkflowManager) -> WorkflowNodeInfo:
    wf, _ = mgr.load_workflow_from_file(StringIO(WORKFLOW_CONFIG))
    mgr.session.flush()
    mgr.session.expire_all()
    return mgr.start_workflow(wf.wf_config_id, entity_id="entity-1")


def test_basic_creation(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_note_node(
        entity_id="entity-1",
        title="My Note",
        started_at=ts,
    )
    assert isinstance(result, NoteNodeInfo)
    assert result.title == "My Note"
    assert result.entity_id == "entity-1"


def test_status_is_finished(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_note_node(entity_id="entity-1", title="Note", started_at=ts)
    assert result.status == NodeStatus.FINISHED


def test_timestamps_all_same(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_note_node(entity_id="entity-1", title="Note", started_at=ts)
    assert result.started_at == ts
    assert result.finished_at == ts
    assert result.deadline == ts


def test_no_config(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_note_node(entity_id="entity-1", title="Note", started_at=ts)
    assert result.wf_config_id is None


def test_note_default_none(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_note_node(entity_id="entity-1", title="Note", started_at=ts)
    assert result.note is None


def test_note_set(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_note_node(
        entity_id="entity-1", title="Note", started_at=ts, note="Some content"
    )
    assert result.note == "Some content"


def test_is_public_default_false(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_note_node(entity_id="entity-1", title="Note", started_at=ts)
    assert result.is_public is False


def test_is_public_true(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_note_node(
        entity_id="entity-1", title="Note", started_at=ts, is_public=True
    )
    assert result.is_public is True


def test_no_parent(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_note_node(entity_id="entity-1", title="Note", started_at=ts)
    assert result.parent_id is None


def test_parent_workflow_node(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_note_node(
        entity_id="entity-1", title="Note", started_at=ts, parent_id=wf_node.wf_node_id
    )
    assert result.parent_id == wf_node.wf_node_id


def test_parent_task_node(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    steps = mgr.get_next_steps(wf_node.wf_node_id)
    task = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    result = mgr.create_note_node(
        entity_id="entity-1", title="Note", started_at=ts, parent_id=task.wf_node_id
    )
    assert result.parent_id == task.wf_node_id


def test_parent_not_found(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    with pytest.raises(WorkflowNotFound):
        mgr.create_note_node(
            entity_id="entity-1", title="Note", started_at=ts, parent_id=99999
        )


def test_parent_walks_up_to_workflow(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    """If reference node is a NoteNode under a workflow, walks up to WorkflowNode."""
    ts = datetime(2024, 6, 15, 10, 0, 0)
    note1 = mgr.create_note_node(
        entity_id="entity-1", title="First", started_at=ts, parent_id=wf_node.wf_node_id
    )
    # Use the note as reference — should walk up to the WorkflowNode
    result = mgr.create_note_node(
        entity_id="entity-1", title="Second", started_at=ts, parent_id=note1.wf_node_id
    )
    assert result.parent_id == wf_node.wf_node_id


def test_parent_no_suitable_ancestor(mgr: WorkflowManager):
    """If reference node has no TaskNode/WorkflowNode ancestor, note is top-level."""
    ts = datetime(2024, 6, 15, 10, 0, 0)
    note1 = mgr.create_note_node(entity_id="entity-1", title="Top", started_at=ts)
    result = mgr.create_note_node(
        entity_id="entity-1", title="Another", started_at=ts, parent_id=note1.wf_node_id
    )
    assert result.parent_id is None


def test_version_is_none(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_note_node(entity_id="entity-1", title="Note", started_at=ts)
    assert result.version is None


def test_url_default_none(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_note_node(entity_id="entity-1", title="Note", started_at=ts)
    assert result.url is None


def test_url_set(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_note_node(
        entity_id="entity-1",
        title="Note",
        started_at=ts,
        url="https://example.com/note",
    )
    assert result.url == "https://example.com/note"
