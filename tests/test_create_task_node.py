"""Tests for WorkflowManager.create_task_node."""

from datetime import datetime
from io import StringIO

import pytest

from business_workflow_manager import (
    TaskNodeInfo,
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
    result = mgr.create_task_node(entity_id="entity-1", title="My Task", started_at=ts)
    assert isinstance(result, TaskNodeInfo)
    assert result.title == "My Task"
    assert result.entity_id == "entity-1"


def test_status_is_started(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_task_node(entity_id="entity-1", title="Task", started_at=ts)
    assert result.status == NodeStatus.STARTED


def test_started_at_set(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15, 10, 0, 0)
    result = mgr.create_task_node(entity_id="entity-1", title="Task", started_at=ts)
    assert result.started_at == ts


def test_finished_at_none(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_task_node(entity_id="entity-1", title="Task", started_at=ts)
    assert result.finished_at is None


def test_deadline(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    dl = datetime(2024, 7, 1)
    result = mgr.create_task_node(
        entity_id="entity-1", title="Task", started_at=ts, deadline=dl
    )
    assert result.deadline == dl


def test_deadline_default_none(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_task_node(entity_id="entity-1", title="Task", started_at=ts)
    assert result.deadline is None


def test_note(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_task_node(
        entity_id="entity-1", title="Task", started_at=ts, note="Important"
    )
    assert result.note == "Important"


def test_no_config(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_task_node(entity_id="entity-1", title="Task", started_at=ts)
    assert result.wf_config_id is None


def test_version_is_none(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_task_node(entity_id="entity-1", title="Task", started_at=ts)
    assert result.version is None


def test_no_parent(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_task_node(entity_id="entity-1", title="Task", started_at=ts)
    assert result.parent_id is None


def test_parent_workflow_node(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    ts = datetime(2024, 6, 15)
    result = mgr.create_task_node(
        entity_id="entity-1", title="Task", started_at=ts, parent_id=wf_node.wf_node_id
    )
    assert result.parent_id == wf_node.wf_node_id


def test_parent_walks_up_to_workflow(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    """If reference is a task under a workflow, walks up to the WorkflowNode."""
    ts = datetime(2024, 6, 15)
    steps = mgr.get_next_steps(wf_node.wf_node_id)
    task = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    # Use the task as reference — should walk up to the WorkflowNode
    result = mgr.create_task_node(
        entity_id="entity-1",
        title="Ad-hoc task",
        started_at=ts,
        parent_id=task.wf_node_id,
    )
    assert result.parent_id == wf_node.wf_node_id


def test_parent_no_suitable_ancestor(mgr: WorkflowManager):
    """If reference node has no WorkflowNode ancestor, task is top-level."""
    ts = datetime(2024, 6, 15)
    # Create a top-level task (no parent)
    task1 = mgr.create_task_node(entity_id="entity-1", title="Top", started_at=ts)
    # Use it as reference — no WorkflowNode ancestor exists
    result = mgr.create_task_node(
        entity_id="entity-1", title="Another", started_at=ts, parent_id=task1.wf_node_id
    )
    assert result.parent_id is None


def test_parent_not_found(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    with pytest.raises(WorkflowNotFound):
        mgr.create_task_node(
            entity_id="entity-1", title="Task", started_at=ts, parent_id=99999
        )


def test_url_default_none(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_task_node(entity_id="entity-1", title="Task", started_at=ts)
    assert result.url is None


def test_url_set(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    result = mgr.create_task_node(
        entity_id="entity-1", title="Task", started_at=ts, url="https://example.com"
    )
    assert result.url == "https://example.com"
