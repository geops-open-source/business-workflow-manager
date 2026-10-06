"""Tests for WorkflowManager.delete_node."""

from datetime import datetime
from io import StringIO

import pytest

from business_workflow_manager import (
    NodeInfo,
    WorkflowException,
    WorkflowManager,
    WorkflowNotFound,
)
from business_workflow_manager.models import Node

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


def _info(mgr: WorkflowManager, node_id: int) -> NodeInfo:
    return NodeInfo.from_model(mgr.session.get_one(Node, node_id))


def _start_task(mgr: WorkflowManager, config: str):
    wf, _ = mgr.load_workflow_from_file(StringIO(config))
    mgr.session.flush()
    mgr.session.expire_all()
    wf_node = mgr.start_workflow(wf.wf_config_id, entity_id="entity-1")
    steps = mgr.get_next_steps(wf_node.wf_node_id)
    return mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)


def test_cannot_delete_node_with_next_node(mgr: WorkflowManager):
    """A document with a next node is not read-only, but cannot be deleted."""
    task = _start_task(mgr, WORKFLOW_CONFIG)
    task_steps = mgr.get_next_steps(task.wf_node_id)
    doc1 = mgr.start_next_step(task.wf_node_id, task_steps[0].wf_config_id)
    mgr.update_document_node(doc1.wf_node_id, document_ref="REF")
    doc1_steps = mgr.get_next_steps(doc1.wf_node_id)
    doc2 = mgr.start_next_step(doc1.wf_node_id, doc1_steps[0].wf_config_id)

    doc1_info = _info(mgr, doc1.wf_node_id)
    assert not doc1_info.is_readonly
    assert not doc1_info.is_deletable
    with pytest.raises(WorkflowException, match="Cannot delete node"):
        mgr.delete_node(doc1.wf_node_id)

    # The last node of the chain can be deleted.
    assert _info(mgr, doc2.wf_node_id).is_deletable
    mgr.delete_node(doc2.wf_node_id)


FORM_WORKFLOW_CONFIG = """\
workflow:
  title: Form workflow
  version: 1
  min_per_entity: 0
  max_per_entity: null
  start_task_ref: task1
  tasks:
    task1:
      title: Task 1
      steps:
        - type: form
          title: Form step
          name: form1
          fields: []
        - type: document
          title: Doc step
      links:
        - task_ref: END
    END:
      title: __END__
      steps: []
      links: []
"""


def _start_form_with_next_node(mgr: WorkflowManager):
    task = _start_task(mgr, FORM_WORKFLOW_CONFIG)
    task_steps = mgr.get_next_steps(task.wf_node_id)
    form = mgr.start_next_step(task.wf_node_id, task_steps[0].wf_config_id)
    mgr.update_form_node(form.wf_node_id, form_data={})
    form_steps = mgr.get_next_steps(form.wf_node_id)
    doc = mgr.start_next_step(form.wf_node_id, form_steps[0].wf_config_id)
    return form, doc


def test_form_with_next_node_is_readonly(mgr: WorkflowManager):
    form, _ = _start_form_with_next_node(mgr)

    form_info = _info(mgr, form.wf_node_id)
    assert form_info.is_readonly
    assert not form_info.is_deletable
    with pytest.raises(WorkflowException, match="Cannot delete node"):
        mgr.delete_node(form.wf_node_id)


def test_form_is_editable_again_after_deleting_next_node(mgr: WorkflowManager):
    form, doc = _start_form_with_next_node(mgr)

    mgr.delete_node(doc.wf_node_id)
    mgr.session.commit()

    form_info = _info(mgr, form.wf_node_id)
    assert not form_info.is_readonly
    assert form_info.is_deletable
    mgr.update_form_node(form.wf_node_id, form_data={"changed": True})
    # The deleted step is offered again.
    assert [s.wf_config_id for s in mgr.get_next_steps(form.wf_node_id)] == [
        doc.wf_config_id
    ]
    mgr.delete_node(form.wf_node_id)


def test_only_forms_can_be_readonly(mgr: WorkflowManager):
    ts = datetime(2024, 6, 15)
    task = _start_task(mgr, WORKFLOW_CONFIG)
    note = mgr.create_note_node(entity_id="entity-1", title="Note", started_at=ts)
    doc = mgr.create_document_node(
        entity_id="entity-1", title="Doc", started_at=ts, document_ref="REF"
    )
    for node_id in (task.wf_node_id, note.wf_node_id, doc.wf_node_id):
        assert not _info(mgr, node_id).is_readonly


def test_delete_not_found(mgr: WorkflowManager):
    with pytest.raises(WorkflowNotFound):
        mgr.delete_node(99999)
