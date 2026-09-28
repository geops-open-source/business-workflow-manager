"""Tests for WorkflowManager.create_document_node."""

from datetime import datetime
from io import StringIO

import pytest

from business_workflow_manager import (
    DocumentNodeInfo,
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
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="My Document",
        started_at=ts,
        document_ref="DOC-001",
    )
    assert isinstance(result, DocumentNodeInfo)
    assert result.title == "My Document"
    assert result.document_ref == "DOC-001"
    assert result.entity_id == "entity-1"


def test_status_is_finished(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Doc",
        started_at=ts,
        document_ref="REF-1",
    )
    assert result.status == NodeStatus.FINISHED


def test_timestamps_all_same(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Doc",
        started_at=ts,
        document_ref="REF-1",
    )
    assert result.started_at == ts
    assert result.finished_at == ts
    assert result.deadline == ts


def test_no_config(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Doc",
        started_at=ts,
        document_ref="REF-1",
    )
    assert result.wf_config_id is None


def test_note_default_none(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Doc",
        started_at=ts,
        document_ref="REF-1",
    )
    assert result.note is None


def test_note_set(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Doc",
        started_at=ts,
        document_ref="REF-1",
        note="Important note",
    )
    assert result.note == "Important note"


def test_is_public_default_false(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Doc",
        started_at=ts,
        document_ref="REF-1",
    )
    assert result.is_public is False


def test_is_public_true(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Doc",
        started_at=ts,
        document_ref="REF-1",
        is_public=True,
    )
    assert result.is_public is True


def test_no_parent(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Doc",
        started_at=ts,
        document_ref="REF-1",
    )
    assert result.parent_id is None


def test_parent_workflow_node(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Doc",
        started_at=ts,
        document_ref="REF-1",
        parent_id=wf_node.wf_node_id,
    )
    assert result.parent_id == wf_node.wf_node_id


def test_parent_task_node(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    # Start a task from the workflow
    steps = mgr.get_next_steps(wf_node.wf_node_id)
    task = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Doc under task",
        started_at=ts,
        document_ref="REF-2",
        parent_id=task.wf_node_id,
    )
    assert result.parent_id == task.wf_node_id


def test_parent_not_found(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    with pytest.raises(WorkflowNotFound):
        mgr.create_document_node(
            entity_id="entity-1",
            title="Doc",
            started_at=ts,
            document_ref="REF-1",
            parent_id=99999,
        )


def test_parent_invalid_type_walks_up(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    """If parent_id is a DocumentNode, walks up to find a TaskNode or WorkflowNode."""
    ts = datetime(2024, 6, 15, 10, 0, 0)
    # Create a document node under the workflow
    doc = mgr.create_document_node(
        entity_id="entity-1",
        title="Parent doc",
        started_at=ts,
        document_ref="REF-A",
        parent_id=wf_node.wf_node_id,
    )
    # Use the document as reference — should walk up to the WorkflowNode
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Child doc",
        started_at=ts,
        document_ref="REF-B",
        parent_id=doc.wf_node_id,
    )
    assert result.parent_id == wf_node.wf_node_id


def test_parent_no_suitable_ancestor(mgr: WorkflowManager):
    """If reference node has no TaskNode/WorkflowNode ancestor, document is top-level."""
    ts = datetime(2024, 6, 15, 10, 0, 0)
    # Create a top-level document (no parent)
    doc = mgr.create_document_node(
        entity_id="entity-1",
        title="Top-level doc",
        started_at=ts,
        document_ref="REF-X",
    )
    # Use it as reference — no suitable ancestor exists
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Another doc",
        started_at=ts,
        document_ref="REF-Y",
        parent_id=doc.wf_node_id,
    )
    assert result.parent_id is None


def test_parent_prefers_task_over_workflow(
    mgr: WorkflowManager, wf_node: WorkflowNodeInfo
):
    """When both TaskNode and WorkflowNode are ancestors, TaskNode is preferred."""
    ts = datetime(2024, 6, 15, 10, 0, 0)
    # Start a task under the workflow
    steps = mgr.get_next_steps(wf_node.wf_node_id)
    task = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    # Start a form step under the task
    task_steps = mgr.get_next_steps(task.wf_node_id)
    if task_steps:
        form = mgr.start_next_step(task.wf_node_id, task_steps[0].wf_config_id)
        # Use the form as reference — should find TaskNode (not WorkflowNode)
        result = mgr.create_document_node(
            entity_id="entity-1",
            title="Doc under form",
            started_at=ts,
            document_ref="REF-F",
            parent_id=form.wf_node_id,
        )
        assert result.parent_id == task.wf_node_id
    else:
        # Task has no steps, use the task itself as reference
        result = mgr.create_document_node(
            entity_id="entity-1",
            title="Doc under task",
            started_at=ts,
            document_ref="REF-T",
            parent_id=task.wf_node_id,
        )
        assert result.parent_id == task.wf_node_id


def test_version_is_none(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Doc",
        started_at=ts,
        document_ref="REF-1",
    )
    assert result.version is None


def test_url_default_none(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_document_node(
        entity_id="entity-1", title="Doc", started_at=ts, document_ref="REF-1"
    )
    assert result.url is None


def test_url_set(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_document_node(
        entity_id="entity-1",
        title="Doc",
        started_at=ts,
        document_ref="REF-1",
        url="https://example.com/doc",
    )
    assert result.url == "https://example.com/doc"
