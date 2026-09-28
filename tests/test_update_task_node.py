"""Tests for WorkflowManager.update_task_node."""

from dataclasses import dataclass
from datetime import datetime
from io import StringIO

import pytest
from sqlalchemy.orm import Session

from business_workflow_manager import (
    CascadeConflict,
    Event,
    NodeInfo,
    WorkflowManager,
    WorkflowManagerConfig,
    WorkflowNodeInfo,
)
from business_workflow_manager.exceptions import WorkflowNotFound
from business_workflow_manager.models import TaskNode, WorkflowNode
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
      triggers:
        - type: TaskDone
          value:
            name: task_done
    END:
      title: __END__
      steps: []
      links: []
"""


@dataclass
class TaskDone(Event):
    name: str


@pytest.fixture
def collected_events() -> list[Event]:
    return []


@pytest.fixture
def mgr(session: Session, collected_events: list[Event]) -> WorkflowManager:
    """Create a WorkflowManager instance for testing."""

    def handle_task_done(session, event, node, context):
        collected_events.append(event)

    config = WorkflowManagerConfig()
    mgr = WorkflowManager(session, config)
    mgr.register_event_handler(TaskDone, handle_task_done)
    return mgr


@pytest.fixture
def workflow_node(mgr: WorkflowManager) -> WorkflowNodeInfo:
    """Create a workflow instance."""
    wf, _ = mgr.load_workflow_from_file(StringIO(WORKFLOW_CONFIG))
    mgr.session.flush()
    mgr.session.expire_all()
    return mgr.start_workflow(wf.wf_config_id, entity_id="entity-1")


@pytest.fixture
def task_node(mgr: WorkflowManager, workflow_node: WorkflowNodeInfo) -> NodeInfo:
    """Create a task_node node under the workflow."""
    steps = mgr.get_next_steps(workflow_node.wf_node_id)
    return mgr.start_next_step(workflow_node.wf_node_id, steps[0].wf_config_id)


def test_update_title(mgr: WorkflowManager, task_node: NodeInfo):
    result = mgr.update_task_node(task_node.wf_node_id, title="New Title")
    assert result.title == "New Title"


def test_update_note(mgr: WorkflowManager, task_node: NodeInfo):
    result = mgr.update_task_node(task_node.wf_node_id, note="A note")
    assert result.note == "A note"


def test_url_default_none(mgr: WorkflowManager, task_node: NodeInfo):
    assert task_node.url is None


def test_url_set(mgr: WorkflowManager, task_node: NodeInfo):
    result = mgr.update_task_node(task_node.wf_node_id, url="https://example.com")
    assert result.url == "https://example.com"


def test_clear_url(mgr: WorkflowManager, task_node: NodeInfo):
    mgr.update_task_node(task_node.wf_node_id, url="https://example.com")
    result = mgr.update_task_node(task_node.wf_node_id, url=None)
    assert result.url is None


def test_url_unchanged_when_omitted(mgr: WorkflowManager, task_node: NodeInfo):
    mgr.update_task_node(task_node.wf_node_id, url="https://example.com")
    result = mgr.update_task_node(task_node.wf_node_id, title="New Title")
    assert result.url == "https://example.com"


def test_update_deadline(mgr: WorkflowManager, task_node: NodeInfo):
    deadline = datetime(2026, 12, 31)
    result = mgr.update_task_node(task_node.wf_node_id, deadline=deadline)
    assert result.deadline == deadline


def test_update_status_to_finished(mgr: WorkflowManager, task_node: NodeInfo):
    result = mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.FINISHED)
    assert result.status == NodeStatus.FINISHED


def test_status_started_clears_finished_at(mgr: WorkflowManager, task_node: NodeInfo):
    mgr.update_task_node(
        task_node.wf_node_id,
        status=NodeStatus.FINISHED,
        finished_at=datetime.now(),
    )
    result = mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.STARTED)
    assert result.status == NodeStatus.STARTED
    assert result.finished_at is None


def test_status_inactive_clears_deadline_and_finished_at(
    mgr: WorkflowManager, task_node: NodeInfo
):
    mgr.update_task_node(
        task_node.wf_node_id,
        deadline=datetime(2026, 6, 1),
        finished_at=datetime.now(),
    )
    result = mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.INACTIVE)
    assert result.status == NodeStatus.INACTIVE
    assert result.deadline is None
    assert result.finished_at is None


def test_cascade_conflict_deadline_later_than_parent(
    mgr: WorkflowManager, workflow_node: WorkflowNodeInfo, task_node: NodeInfo
):
    """Task deadline later than parent's deadline raises CascadeConflict."""
    # Set parent deadline to June 1
    mgr.update_workflow_node(workflow_node.wf_node_id, deadline=datetime(2026, 6, 1))

    # Try to set task_node deadline later than parent's
    with pytest.raises(CascadeConflict) as exc_info:
        mgr.update_task_node(
            task_node.wf_node_id,
            deadline=datetime(2026, 12, 31),
            cascade=False,
        )

    assert any(
        n.wf_node_id == workflow_node.wf_node_id
        for n in exc_info.value.deadline_conflicts
    )


def test_cascade_conflict_reopening_under_finished_parent(
    mgr: WorkflowManager, workflow_node: WorkflowNodeInfo, task_node: NodeInfo
):
    """Setting task_node to non-finished while parent is finished raises CascadeConflict."""

    # Finish the task_node first, then finish the workflow
    mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.FINISHED)
    mgr.update_workflow_node(workflow_node.wf_node_id, status=NodeStatus.FINISHED)

    # Try to reopen the task_node
    with pytest.raises(CascadeConflict) as exc_info:
        mgr.update_task_node(
            task_node.wf_node_id,
            status=NodeStatus.STARTED,
            cascade=False,
        )

    assert any(
        n.wf_node_id == workflow_node.wf_node_id
        for n in exc_info.value.status_conflicts
    )


def test_cascade_true_extends_parent_deadline(
    mgr: WorkflowManager, workflow_node: WorkflowNodeInfo, task_node: NodeInfo
):
    """With cascade=True, parent deadline is extended."""
    mgr.update_workflow_node(workflow_node.wf_node_id, deadline=datetime(2026, 6, 1))

    new_deadline = datetime(2026, 12, 31)
    mgr.update_task_node(task_node.wf_node_id, deadline=new_deadline, cascade=True)

    orm_wf = mgr.session.get_one(WorkflowNode, workflow_node.wf_node_id)
    assert orm_wf.deadline == new_deadline


def test_cascade_true_reopens_finished_parent(
    mgr: WorkflowManager, workflow_node: WorkflowNodeInfo, task_node: NodeInfo
):
    """With cascade=True, finished parent is reopened."""

    # Finish task_node and workflow
    mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.FINISHED)
    mgr.update_workflow_node(workflow_node.wf_node_id, status=NodeStatus.FINISHED)

    # Reopen task_node with cascade
    mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.STARTED, cascade=True)

    orm_wf = mgr.session.get_one(WorkflowNode, workflow_node.wf_node_id)
    assert orm_wf.status == NodeStatus.STARTED


def test_no_conflict_if_parent_not_finished(
    mgr: WorkflowManager, workflow_node: WorkflowNodeInfo, task_node: NodeInfo
):
    """No conflict when setting non-finished status and parent is also not finished."""
    # Parent is STARTED, task_node is STARTED — setting to INACTIVE should not conflict
    result = mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.INACTIVE)
    assert result.status == NodeStatus.INACTIVE


def test_no_conflict_if_deadline_within_parent(
    mgr: WorkflowManager, workflow_node: WorkflowNodeInfo, task_node: NodeInfo
):
    """No conflict when task_node deadline is within parent's deadline."""
    mgr.update_workflow_node(workflow_node.wf_node_id, deadline=datetime(2026, 12, 31))

    # Task deadline earlier than parent — no conflict
    result = mgr.update_task_node(
        task_node.wf_node_id,
        deadline=datetime(2026, 6, 1),
        cascade=False,
    )
    assert result.deadline == datetime(2026, 6, 1)


def test_not_a_task_node_raises(
    mgr: WorkflowManager, workflow_node: WorkflowNodeInfo, task_node: NodeInfo
):
    """Passing a WorkflowNode id should raise."""
    with pytest.raises(WorkflowNotFound, match="not a TaskNode"):
        mgr.update_task_node(workflow_node.wf_node_id, title="nope")


def test_finished_parent_no_conflict_when_finishing_task(
    mgr: WorkflowManager, workflow_node: WorkflowNodeInfo, task_node: NodeInfo
):
    """No conflict when finishing a task_node under a finished parent (both finished)."""
    mgr.session.get_one(TaskNode, task_node.wf_node_id).events_triggered = True
    mgr.update_workflow_node(
        workflow_node.wf_node_id, status=NodeStatus.FINISHED, cascade=True
    )
    # Task is now finished too — finishing it again should be fine
    result = mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.FINISHED)
    assert result.status == NodeStatus.FINISHED


def test_updating_task_with_status_finished_triggers_events_once(
    mgr: WorkflowManager,
    task_node: NodeInfo,
    collected_events: list[Event],
):
    assert collected_events == []

    mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.SKIPPED)
    assert collected_events == []

    mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.FINISHED)
    assert collected_events == [TaskDone("task_done")]

    mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.STARTED)
    assert collected_events == [TaskDone("task_done")]

    mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.FINISHED)
    assert collected_events == [TaskDone("task_done")]
