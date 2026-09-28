"""Tests for WorkflowManager.update_workflow_node."""

from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import cast

import pytest
from sqlalchemy.orm import Session

from business_workflow_manager import (
    CascadeConflict,
    Event,
    WorkflowManager,
    WorkflowManagerConfig,
    WorkflowNodeInfo,
)
from business_workflow_manager.exceptions import WorkflowNotFound
from business_workflow_manager.models import Node, TaskNode, Workflow, WorkflowNode
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


def test_update_title(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    result = mgr.update_workflow_node(wf_node.wf_node_id, title="New Title")
    assert result.title == "New Title"


def test_update_note(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    result = mgr.update_workflow_node(wf_node.wf_node_id, note="A note")
    assert result.note == "A note"


def test_clear_note(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    mgr.update_workflow_node(wf_node.wf_node_id, note="A note")
    result = mgr.update_workflow_node(wf_node.wf_node_id, note=None)
    assert result.note is None


def test_url_default_none(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    assert wf_node.url is None


def test_url_set(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    result = mgr.update_workflow_node(wf_node.wf_node_id, url="https://example.com")
    assert result.url == "https://example.com"


def test_clear_url(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    mgr.update_workflow_node(wf_node.wf_node_id, url="https://example.com")
    result = mgr.update_workflow_node(wf_node.wf_node_id, url=None)
    assert result.url is None


def test_url_unchanged_when_omitted(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    mgr.update_workflow_node(wf_node.wf_node_id, url="https://example.com")
    result = mgr.update_workflow_node(wf_node.wf_node_id, title="New Title")
    assert result.url == "https://example.com"


def test_update_deadline(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    deadline = datetime(2026, 12, 31)
    result = mgr.update_workflow_node(wf_node.wf_node_id, deadline=deadline)
    assert result.deadline == deadline


def test_update_status_to_finished(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    result = mgr.update_workflow_node(wf_node.wf_node_id, status=NodeStatus.FINISHED)
    assert result.status == NodeStatus.FINISHED


def test_status_started_clears_finished_at(
    mgr: WorkflowManager, wf_node: WorkflowNodeInfo
):
    # First finish
    mgr.update_workflow_node(
        wf_node.wf_node_id,
        status=NodeStatus.FINISHED,
        finished_at=datetime.now(),
    )
    # Then reopen — finished_at should be cleared
    result = mgr.update_workflow_node(wf_node.wf_node_id, status=NodeStatus.STARTED)
    assert result.status == NodeStatus.STARTED
    assert result.finished_at is None


def test_status_inactive_clears_deadline_and_finished_at(
    mgr: WorkflowManager, wf_node: WorkflowNodeInfo
):
    mgr.update_workflow_node(
        wf_node.wf_node_id,
        deadline=datetime(2026, 6, 1),
        finished_at=datetime.now(),
    )
    result = mgr.update_workflow_node(wf_node.wf_node_id, status=NodeStatus.INACTIVE)
    assert result.status == NodeStatus.INACTIVE
    assert result.deadline is None
    assert result.finished_at is None


def test_no_op_when_status_unchanged(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    """Setting same status should not clear fields."""
    deadline = datetime(2026, 6, 1)
    mgr.update_workflow_node(wf_node.wf_node_id, deadline=deadline)
    result = mgr.update_workflow_node(wf_node.wf_node_id, status=NodeStatus.STARTED)
    # Status was already STARTED — no side effects
    assert result.deadline == deadline


def test_cascade_conflict_deadline(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    """Children with later deadlines raise CascadeConflict."""
    # Create a child task node with a later deadline
    orm_wf = mgr.session.get_one(WorkflowNode, wf_node.wf_node_id)
    assert orm_wf.config is not None
    child = TaskNode(
        config=list(cast(Workflow, orm_wf.config).tasks.values())[0],
        title="Child",
        version=1,
        entity_id="entity-1",
        status=NodeStatus.STARTED,
        deadline=datetime(2026, 12, 31),
    )
    child.parent = orm_wf
    mgr.session.add(child)
    mgr.session.flush()

    # Try to set a deadline earlier than the child's
    with pytest.raises(CascadeConflict) as exc_info:
        mgr.update_workflow_node(
            wf_node.wf_node_id,
            deadline=datetime(2026, 6, 1),
            cascade=False,
        )

    assert any(
        n.wf_node_id == child.wf_node_id for n in exc_info.value.deadline_conflicts
    )


def test_cascade_conflict_status_finished(
    mgr: WorkflowManager, wf_node: WorkflowNodeInfo
):
    """Children still started raise CascadeConflict when finishing parent."""
    orm_wf = mgr.session.get_one(WorkflowNode, wf_node.wf_node_id)
    assert orm_wf.config is not None
    child = TaskNode(
        config=list(cast(Workflow, orm_wf.config).tasks.values())[0],
        title="Child",
        version=1,
        entity_id="entity-1",
        status=NodeStatus.STARTED,
    )
    child.parent = orm_wf
    mgr.session.add(child)
    mgr.session.flush()

    with pytest.raises(CascadeConflict) as exc_info:
        mgr.update_workflow_node(
            wf_node.wf_node_id,
            status=NodeStatus.FINISHED,
            cascade=False,
        )

    assert any(
        n.wf_node_id == child.wf_node_id for n in exc_info.value.status_conflicts
    )


def test_cascade_true_propagates_deadline(
    mgr: WorkflowManager, wf_node: WorkflowNodeInfo
):
    """With cascade=True, child deadlines are updated."""
    orm_wf = mgr.session.get_one(WorkflowNode, wf_node.wf_node_id)
    assert orm_wf.config is not None
    child = TaskNode(
        config=list(cast(Workflow, orm_wf.config).tasks.values())[0],
        title="Child",
        version=1,
        entity_id="entity-1",
        status=NodeStatus.STARTED,
        deadline=datetime(2026, 12, 31),
    )
    child.parent = orm_wf
    mgr.session.add(child)
    mgr.session.flush()

    new_deadline = datetime(2026, 6, 1)
    mgr.update_workflow_node(wf_node.wf_node_id, deadline=new_deadline, cascade=True)

    mgr.session.expire(child)
    assert child.deadline == new_deadline


def test_cascade_true_finishes_children(
    mgr: WorkflowManager, wf_node: WorkflowNodeInfo
):
    """With cascade=True, open children are finished."""
    orm_wf = mgr.session.get_one(WorkflowNode, wf_node.wf_node_id)
    assert orm_wf.config is not None
    child = TaskNode(
        config=list(cast(Workflow, orm_wf.config).tasks.values())[0],
        title="Child",
        version=1,
        entity_id="entity-1",
        status=NodeStatus.STARTED,
    )
    child.parent = orm_wf
    mgr.session.add(child)
    mgr.session.flush()

    end_time = datetime(2026, 7, 1)
    mgr.update_workflow_node(
        wf_node.wf_node_id,
        status=NodeStatus.FINISHED,
        finished_at=end_time,
        cascade=True,
    )

    mgr.session.expire(child)
    assert child.status == NodeStatus.FINISHED
    assert child.finished_at == end_time


def test_cascade_grandchildren(mgr: WorkflowManager, wf_node: WorkflowNodeInfo):
    """Cascade applies recursively to grandchildren."""
    orm_wf = mgr.session.get_one(WorkflowNode, wf_node.wf_node_id)
    assert orm_wf.config is not None
    child = TaskNode(
        config=list(cast(Workflow, orm_wf.config).tasks.values())[0],
        title="Child",
        version=1,
        entity_id="entity-1",
        status=NodeStatus.STARTED,
    )
    child.parent = orm_wf
    mgr.session.add(child)
    mgr.session.flush()

    grandchild = TaskNode(
        config=list(cast(Workflow, orm_wf.config).tasks.values())[0],
        title="Grandchild",
        version=1,
        entity_id="entity-1",
        status=NodeStatus.STARTED,
    )
    grandchild.parent = child
    mgr.session.add(grandchild)
    mgr.session.flush()

    mgr.update_workflow_node(
        wf_node.wf_node_id,
        status=NodeStatus.FINISHED,
        cascade=True,
    )

    mgr.session.expire(grandchild)
    assert grandchild.status == NodeStatus.FINISHED


def test_finished_children_not_affected_by_deadline_cascade(
    mgr: WorkflowManager, wf_node: WorkflowNodeInfo
):
    """Finished children are not flagged as deadline conflicts."""
    orm_wf = mgr.session.get_one(WorkflowNode, wf_node.wf_node_id)
    assert orm_wf.config is not None
    child = TaskNode(
        config=list(cast(Workflow, orm_wf.config).tasks.values())[0],
        title="Finished Child",
        version=1,
        entity_id="entity-1",
        status=NodeStatus.FINISHED,
        deadline=datetime(2026, 12, 31),
    )
    child.parent = orm_wf
    mgr.session.add(child)
    mgr.session.flush()

    # Should not raise — child is already finished
    result = mgr.update_workflow_node(
        wf_node.wf_node_id,
        deadline=datetime(2026, 6, 1),
        cascade=False,
    )
    assert result.deadline == datetime(2026, 6, 1)


def test_update_not_a_workflow_node_raises(
    mgr: WorkflowManager, wf_node: WorkflowNodeInfo
):
    """Passing a TaskNode id should raise."""
    orm_wf = mgr.session.get_one(WorkflowNode, wf_node.wf_node_id)
    assert orm_wf.config is not None
    child = TaskNode(
        config=list(cast(Workflow, orm_wf.config).tasks.values())[0],
        title="Child",
        version=1,
        entity_id="entity-1",
        status=NodeStatus.STARTED,
    )
    child.parent = orm_wf
    mgr.session.add(child)
    mgr.session.flush()

    with pytest.raises(WorkflowNotFound, match="not a WorkflowNode"):
        mgr.update_workflow_node(child.wf_node_id, title="nope")


# ---------------------------------------------------------------------------
# Pending-trigger conflict tests
# ---------------------------------------------------------------------------

WORKFLOW_CONFIG_WITH_TRIGGERS = """\
workflow:
  title: Test workflow with triggers
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
def mgr_with_triggers(
    session: Session, collected_events: list[Event]
) -> WorkflowManager:
    def handler(_s: Session, e: Event, _n: Node, _ctx: object) -> None:
        collected_events.append(e)

    mgr = WorkflowManager(
        session,
        WorkflowManagerConfig(),
    )
    mgr.register_event_handler(TaskDone, handler)
    return mgr


@pytest.fixture
def wf_node_with_triggers(
    mgr_with_triggers: WorkflowManager,
) -> WorkflowNodeInfo:
    wf, _ = mgr_with_triggers.load_workflow_from_file(
        StringIO(WORKFLOW_CONFIG_WITH_TRIGGERS)
    )
    mgr_with_triggers.session.flush()
    mgr_with_triggers.session.expire_all()
    return mgr_with_triggers.start_workflow(wf.wf_config_id, entity_id="entity-1")


def _start_task_with_triggers(
    mgr: WorkflowManager, wf_node: WorkflowNodeInfo
) -> TaskNode:
    """Start the first task step and return the ORM object (status=STARTED, events_triggered=False)."""
    steps = mgr.get_next_steps(wf_node.wf_node_id)
    task_info = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)
    return mgr.session.get_one(TaskNode, task_info.wf_node_id)


def test_pending_trigger_conflict_raises(
    mgr_with_triggers: WorkflowManager,
    wf_node_with_triggers: WorkflowNodeInfo,
) -> None:
    """Finishing a workflow is blocked when a child task is open and has unfired triggers."""
    orm_task = _start_task_with_triggers(mgr_with_triggers, wf_node_with_triggers)

    with pytest.raises(CascadeConflict) as exc_info:
        mgr_with_triggers.update_workflow_node(
            wf_node_with_triggers.wf_node_id,
            status=NodeStatus.FINISHED,
            cascade=False,
        )
    conflict = exc_info.value
    assert any(
        n.wf_node_id == orm_task.wf_node_id for n in conflict.pending_trigger_conflicts
    )


def test_pending_trigger_conflict_not_raised_when_no_triggers(
    mgr: WorkflowManager,
    wf_node: WorkflowNodeInfo,
) -> None:
    """STARTED child tasks without configured triggers do not appear in pending_trigger_conflicts."""
    orm_wf = mgr.session.get_one(WorkflowNode, wf_node.wf_node_id)
    assert orm_wf.config is not None
    child = TaskNode(
        config=list(cast(Workflow, orm_wf.config).tasks.values())[0],
        title="Child",
        version=1,
        entity_id="entity-1",
        status=NodeStatus.STARTED,
    )
    child.parent = orm_wf
    mgr.session.add(child)
    mgr.session.flush()

    # WORKFLOW_CONFIG has no triggers on its tasks — only a status conflict, not a trigger conflict
    with pytest.raises(CascadeConflict) as exc_info:
        mgr.update_workflow_node(
            wf_node.wf_node_id, status=NodeStatus.FINISHED, cascade=False
        )

    assert exc_info.value.pending_trigger_conflicts == []
    assert any(
        n.wf_node_id == child.wf_node_id for n in exc_info.value.status_conflicts
    )


def test_pending_trigger_conflict_not_raised_when_already_triggered(
    mgr_with_triggers: WorkflowManager,
    wf_node_with_triggers: WorkflowNodeInfo,
) -> None:
    """No pending trigger conflict when events_triggered is already True."""
    orm_task = _start_task_with_triggers(mgr_with_triggers, wf_node_with_triggers)
    orm_task.events_triggered = True  # mark as already fired
    mgr_with_triggers.session.flush()

    # Task is still STARTED → still a status conflict, but NOT a pending trigger conflict
    with pytest.raises(CascadeConflict) as exc_info:
        mgr_with_triggers.update_workflow_node(
            wf_node_with_triggers.wf_node_id,
            status=NodeStatus.FINISHED,
            cascade=False,
        )

    assert exc_info.value.pending_trigger_conflicts == []
    assert any(
        n.wf_node_id == orm_task.wf_node_id for n in exc_info.value.status_conflicts
    )


def test_cascade_fires_pending_triggers_but_does_not_close_children(
    mgr_with_triggers: WorkflowManager,
    wf_node_with_triggers: WorkflowNodeInfo,
    collected_events: list[Event],
) -> None:
    """cascade=True finishes the open child task and fires its pending triggers."""
    orm_task = _start_task_with_triggers(mgr_with_triggers, wf_node_with_triggers)

    with pytest.raises(CascadeConflict):
        mgr_with_triggers.update_workflow_node(
            wf_node_with_triggers.wf_node_id,
            status=NodeStatus.FINISHED,
            cascade=True,
        )

    assert orm_task.status != NodeStatus.FINISHED
    assert orm_task.events_triggered is False
    assert collected_events == []
