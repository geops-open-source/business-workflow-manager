"""Tests for WorkflowManager.delete_node."""

from datetime import datetime
from io import StringIO

import pytest

from business_workflow_manager import (
    WorkflowException,
    WorkflowManager,
    WorkflowNotFound,
)

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
      steps:
        - type: document
          title: Doc step 1
        - type: document
          title: Doc step 2
      links:
        - task_ref: END
    END:
      title: __END__
      steps: []
      links: []
"""


def test_delete_top_level_document(mgr: WorkflowManager):
    """Can delete a top-level ad-hoc document node."""
    ts = datetime(2024, 6, 15)
    doc = mgr.create_document_node(
        entity_id="entity-1", title="Doc", started_at=ts, document_ref="REF"
    )
    mgr.delete_node(doc.wf_node_id)
    # Verify it's gone
    with pytest.raises(WorkflowNotFound):
        mgr.delete_node(doc.wf_node_id)


def test_delete_top_level_note(mgr: WorkflowManager):
    """Can delete a top-level ad-hoc note node."""
    ts = datetime(2024, 6, 15)
    note = mgr.create_note_node(entity_id="entity-1", title="Note", started_at=ts)
    mgr.delete_node(note.wf_node_id)
    with pytest.raises(WorkflowNotFound):
        mgr.delete_node(note.wf_node_id)


def test_delete_task_without_children(mgr: WorkflowManager):
    """Can delete a task node that has no children."""
    wf, _ = mgr.load_workflow_from_file(StringIO(WORKFLOW_CONFIG))
    mgr.session.flush()
    mgr.session.expire_all()
    wf_node = mgr.start_workflow(wf.wf_config_id, entity_id="entity-1")
    steps = mgr.get_next_steps(wf_node.wf_node_id)
    task = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)
    mgr.delete_node(task.wf_node_id)


def test_cannot_delete_node_with_children(mgr: WorkflowManager):
    """Cannot delete a node that has children."""
    wf, _ = mgr.load_workflow_from_file(StringIO(WORKFLOW_CONFIG))
    mgr.session.flush()
    mgr.session.expire_all()
    wf_node = mgr.start_workflow(wf.wf_config_id, entity_id="entity-1")
    # Start a task (child of wf_node)
    steps = mgr.get_next_steps(wf_node.wf_node_id)
    mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    with pytest.raises(WorkflowException, match="has children"):
        mgr.delete_node(wf_node.wf_node_id)


def test_cannot_delete_readonly_node(mgr: WorkflowManager):
    """Cannot delete a document node that has a next_node (readonly)."""
    wf, _ = mgr.load_workflow_from_file(StringIO(WORKFLOW_CONFIG))
    mgr.session.flush()
    mgr.session.expire_all()
    wf_node = mgr.start_workflow(wf.wf_config_id, entity_id="entity-1")
    steps = mgr.get_next_steps(wf_node.wf_node_id)
    task = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    # Start the first document step
    task_steps = mgr.get_next_steps(task.wf_node_id)
    doc1 = mgr.start_next_step(task.wf_node_id, task_steps[0].wf_config_id)

    # Finish it so the next step becomes available
    mgr.update_document_node(doc1.wf_node_id, document_ref="REF")

    # Start the second document step — this makes doc1 readonly (sets next_node)
    doc1_steps = mgr.get_next_steps(doc1.wf_node_id)
    mgr.start_next_step(doc1.wf_node_id, doc1_steps[0].wf_config_id)

    with pytest.raises(WorkflowException, match="read-only"):
        mgr.delete_node(doc1.wf_node_id)


def test_delete_not_found(mgr: WorkflowManager):
    with pytest.raises(WorkflowNotFound):
        mgr.delete_node(99999)
