"""Tests for WorkflowManager.update_form_node."""

from datetime import datetime
from io import StringIO

import pytest

from business_workflow_manager import FormNodeInfo, WorkflowManager, WorkflowNotFound
from business_workflow_manager.models import Node
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
      steps:
        - type: form
          title: Assessment
          name: assessment
          fields:
            - name: approved
              type: boolean
              label: Approved?
      links:
        - task_ref: END
    END:
      title: __END__
      steps: []
      links: []
"""


@pytest.fixture
def form_node(mgr: WorkflowManager) -> FormNodeInfo:
    wf, _ = mgr.load_workflow_from_file(StringIO(WORKFLOW_CONFIG))
    mgr.session.flush()
    mgr.session.expire_all()
    wf_node = mgr.start_workflow(wf.wf_config_id, entity_id="entity-1")
    steps = mgr.get_next_steps(wf_node.wf_node_id)
    task = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)
    task_steps = mgr.get_next_steps(task.wf_node_id)
    node = mgr.start_next_step(task.wf_node_id, task_steps[0].wf_config_id)
    assert isinstance(node, FormNodeInfo)
    return node


def test_update_title(mgr: WorkflowManager, form_node: FormNodeInfo):
    result = mgr.update_form_node(form_node.wf_node_id, title="New Title")
    assert result.title == "New Title"


def test_update_started_at(mgr: WorkflowManager, form_node: FormNodeInfo):
    new_ts = datetime(2025, 1, 1, 12, 0, 0)
    result = mgr.update_form_node(form_node.wf_node_id, started_at=new_ts)
    assert result.started_at == new_ts


def test_update_form_data(mgr: WorkflowManager, form_node: FormNodeInfo):
    result = mgr.update_form_node(form_node.wf_node_id, form_data={"approved": True})
    assert result.form_data == {"approved": True}


def test_form_data_propagated_to_node_data(
    mgr: WorkflowManager, form_node: FormNodeInfo
):
    """form_data is stored in node.data['form'][form_name] for conditions."""
    mgr.update_form_node(form_node.wf_node_id, form_data={"approved": True})
    # Re-read to check data field
    node = mgr.session.get_one(Node, form_node.wf_node_id)
    assert node.data["form"]["assessment"] == {"approved": True}


def test_update_note(mgr: WorkflowManager, form_node: FormNodeInfo):
    result = mgr.update_form_node(form_node.wf_node_id, note="A note")
    assert result.note == "A note"


def test_clear_note(mgr: WorkflowManager, form_node: FormNodeInfo):
    mgr.update_form_node(form_node.wf_node_id, note="A note")
    result = mgr.update_form_node(form_node.wf_node_id, note=None)
    assert result.note is None


def test_status_always_finished(mgr: WorkflowManager, form_node: FormNodeInfo):
    result = mgr.update_form_node(form_node.wf_node_id, title="X")
    assert result.status == NodeStatus.FINISHED


def test_unchanged_fields_preserved(mgr: WorkflowManager, form_node: FormNodeInfo):
    original_started = form_node.started_at
    result = mgr.update_form_node(form_node.wf_node_id, title="New Title")
    assert result.started_at == original_started


def test_multiple_fields(mgr: WorkflowManager, form_node: FormNodeInfo):
    new_ts = datetime(2025, 3, 1)
    result = mgr.update_form_node(
        form_node.wf_node_id,
        title="Updated",
        started_at=new_ts,
        form_data={"approved": False},
        note="Rejected",
    )
    assert result.title == "Updated"
    assert result.started_at == new_ts
    assert result.form_data == {"approved": False}
    assert result.note == "Rejected"
    assert result.status == NodeStatus.FINISHED


def test_returns_form_node_info(mgr: WorkflowManager, form_node: FormNodeInfo):
    result = mgr.update_form_node(form_node.wf_node_id, title="X")
    assert isinstance(result, FormNodeInfo)


def test_not_found(mgr: WorkflowManager):
    with pytest.raises(WorkflowNotFound):
        mgr.update_form_node(99999, title="X")


def test_wrong_node_type(mgr: WorkflowManager, form_node: FormNodeInfo):
    """Raises WorkflowNotFound if the node is not a FormNode."""
    wf, _ = mgr.load_workflow_from_file(StringIO(WORKFLOW_CONFIG))
    mgr.session.flush()
    mgr.session.expire_all()
    wf_node = mgr.start_workflow(wf.wf_config_id, entity_id="entity-2")

    with pytest.raises(WorkflowNotFound, match="not a FormNode"):
        mgr.update_form_node(wf_node.wf_node_id, title="X")


def test_url_default_none(mgr: WorkflowManager, form_node: FormNodeInfo):
    assert form_node.url is None


def test_url_set(mgr: WorkflowManager, form_node: FormNodeInfo):
    result = mgr.update_form_node(form_node.wf_node_id, url="https://example.com/form")
    assert result.url == "https://example.com/form"


def test_clear_url(mgr: WorkflowManager, form_node: FormNodeInfo):
    mgr.update_form_node(form_node.wf_node_id, url="https://example.com/form")
    result = mgr.update_form_node(form_node.wf_node_id, url=None)
    assert result.url is None


def test_url_unchanged_when_omitted(mgr: WorkflowManager, form_node: FormNodeInfo):
    mgr.update_form_node(form_node.wf_node_id, url="https://example.com/form")
    result = mgr.update_form_node(form_node.wf_node_id, title="New Title")
    assert result.url == "https://example.com/form"
