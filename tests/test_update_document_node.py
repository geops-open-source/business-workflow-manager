"""Tests for WorkflowManager.update_document_node."""

from datetime import datetime
from io import StringIO

import pytest
from sqlalchemy.orm import Session

from business_workflow_manager import (
    DocumentNodeInfo,
    WorkflowManager,
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
def doc_node(mgr: WorkflowManager) -> DocumentNodeInfo:
    ts = datetime(2024, 6, 15, 10, 0, 0)
    return mgr.create_document_node(
        entity_id="entity-1",
        title="Original Title",
        started_at=ts,
        document_ref="DOC-001",
        note="Original note",
        is_public=False,
    )


def test_update_title(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    result = mgr.update_document_node(doc_node.wf_node_id, title="New Title")
    assert result.title == "New Title"


def test_update_started_at(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    new_ts = datetime(2025, 1, 1, 12, 0, 0)
    result = mgr.update_document_node(doc_node.wf_node_id, started_at=new_ts)
    assert result.started_at == new_ts


def test_update_note(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    result = mgr.update_document_node(doc_node.wf_node_id, note="Updated note")
    assert result.note == "Updated note"


def test_clear_note(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    result = mgr.update_document_node(doc_node.wf_node_id, note=None)
    assert result.note is None


def test_update_document_ref(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    result = mgr.update_document_node(doc_node.wf_node_id, document_ref="DOC-002")
    assert result.document_ref == "DOC-002"


def test_clear_document_ref(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    result = mgr.update_document_node(doc_node.wf_node_id, document_ref=None)
    assert result.document_ref is None


def test_update_is_public(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    result = mgr.update_document_node(doc_node.wf_node_id, is_public=True)
    assert result.is_public is True


def test_status_always_finished(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    """Updating a document always sets status to FINISHED."""
    result = mgr.update_document_node(doc_node.wf_node_id, title="Changed")
    assert result.status == NodeStatus.FINISHED


def test_unchanged_fields_preserved(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    """Fields not passed remain unchanged."""
    result = mgr.update_document_node(doc_node.wf_node_id, title="New Title")
    assert result.document_ref == "DOC-001"
    assert result.note == "Original note"
    assert result.is_public is False


def test_multiple_fields(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    new_ts = datetime(2025, 3, 1)
    result = mgr.update_document_node(
        doc_node.wf_node_id,
        title="Updated",
        started_at=new_ts,
        document_ref="DOC-NEW",
        note="New note",
        is_public=True,
    )
    assert result.title == "Updated"
    assert result.started_at == new_ts
    assert result.document_ref == "DOC-NEW"
    assert result.note == "New note"
    assert result.is_public is True


def test_returns_document_node_info(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    result = mgr.update_document_node(doc_node.wf_node_id, title="X")
    assert isinstance(result, DocumentNodeInfo)


def test_not_found(mgr: WorkflowManager):
    with pytest.raises(WorkflowNotFound):
        mgr.update_document_node(99999, title="X")


def test_wrong_node_type(mgr: WorkflowManager, session: Session):
    """Raises WorkflowNotFound if the node is not a DocumentNode."""
    mgr2 = WorkflowManager(session)
    wf, _ = mgr2.load_workflow_from_file(StringIO(WORKFLOW_CONFIG))
    mgr2.session.flush()
    mgr2.session.expire_all()
    wf_node = mgr2.start_workflow(wf.wf_config_id, entity_id="entity-1")

    with pytest.raises(WorkflowNotFound, match="not a DocumentNode"):
        mgr2.update_document_node(wf_node.wf_node_id, title="X")


def test_url_default_none(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    assert doc_node.url is None


def test_url_set(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    result = mgr.update_document_node(
        doc_node.wf_node_id, url="https://example.com/doc"
    )
    assert result.url == "https://example.com/doc"


def test_clear_url(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    mgr.update_document_node(doc_node.wf_node_id, url="https://example.com/doc")
    result = mgr.update_document_node(doc_node.wf_node_id, url=None)
    assert result.url is None


def test_url_unchanged_when_omitted(mgr: WorkflowManager, doc_node: DocumentNodeInfo):
    mgr.update_document_node(doc_node.wf_node_id, url="https://example.com/doc")
    result = mgr.update_document_node(doc_node.wf_node_id, title="New Title")
    assert result.url == "https://example.com/doc"
