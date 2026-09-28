"""Tests for AuditMixin (created_at / updated_at timestamps)."""

from datetime import datetime
from io import StringIO

from business_workflow_manager import WorkflowManager
from business_workflow_manager.models import Node

SIMPLE_WORKFLOW = """\
workflow:
  title: Test
  version: 1
  start_task_ref: t1
  min_per_entity: 0
  max_per_entity: null
  tasks:
    t1:
      title: Task
      steps: []
      links: []
"""


def test_created_at_set_on_insert(mgr: WorkflowManager):
    wf, _ = mgr.load_workflow_from_file(StringIO(SIMPLE_WORKFLOW))
    mgr.session.flush()
    mgr.session.expire_all()

    before = datetime.now()
    node_info = mgr.start_workflow(wf.wf_config_id, entity_id="entity-1")
    after = datetime.now()

    assert node_info.created_at is not None
    assert before <= node_info.created_at <= after


def test_updated_at_none_on_insert(mgr: WorkflowManager):
    wf, _ = mgr.load_workflow_from_file(StringIO(SIMPLE_WORKFLOW))
    mgr.session.flush()
    mgr.session.expire_all()

    node_info = mgr.start_workflow(wf.wf_config_id, entity_id="entity-1")

    assert node_info.updated_at is None


def test_updated_at_set_on_update(mgr: WorkflowManager):
    wf, _ = mgr.load_workflow_from_file(StringIO(SIMPLE_WORKFLOW))
    mgr.session.flush()
    mgr.session.expire_all()

    node_info = mgr.start_workflow(wf.wf_config_id, entity_id="entity-1")

    before = datetime.now()
    orm_node = mgr.session.get_one(Node, node_info.wf_node_id)
    orm_node.title = "updated"
    mgr.session.flush()
    after = datetime.now()

    # Re-fetch the ORM node to check updated_at
    mgr.session.expire(orm_node)
    assert orm_node.updated_at is not None
    assert before <= orm_node.updated_at <= after
