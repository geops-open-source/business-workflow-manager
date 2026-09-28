"""Tests for business_workflow_manager.loader (load/dump workflow YAML ↔ DB)."""

import re
from io import StringIO
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from sqlalchemy.orm import Session

from business_workflow_manager import (
    Event,
    EventHandlerNotRegistered,
    WorkflowManager,
    WorkflowManagerConfig,
)
from business_workflow_manager.loader import get_translations
from business_workflow_manager.models import Form, Node

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


def _extract_readme_workflow() -> str:
    text = README.read_text()
    match = re.search(
        r"<!-- workflow-example-start -->\n```yaml\n(.+?)```\n<!-- workflow-example-end -->",
        text,
        re.DOTALL,
    )
    assert match, "No workflow example block found in README.md"
    return match.group(1)


SIMPLE_WORKFLOW = """\
workflow:
  key: f222bcc9-e563-4014-acb3-f696860f9a0f
  title:
    de: Auskunft (de)
    fr: Auskunft (fr)
    it: Auskunft (it)
  version: 1
  start_task_ref: anfrage
  min_per_entity: 0
  max_per_entity: null
  tasks:
    anfrage:
      key: 2e310749-c2ed-4c2c-b26a-568a4e61c1d3
      title: Anfrage
      start_task: true
      steps:
        - key: bc0fad32-1e23-40e7-9129-4a806637b542
          title: Anfrage Dokument
          type: document
      links:
        - task_ref: END
    END:
      key: dcbcf684-7365-41ce-a305-66c66de85401
      title: __END__
      links: []
"""

EXTENDED_WORKFLOW = """\
workflow:
  key: d253b732-5b06-431c-88d9-e4daa9217819
  title: Extended Workflow
  version: 1
  start_task_ref: task1
  min_per_entity: 0
  max_per_entity: null
  time_period: 14
  tasks:
    task1:
      key: e78b99f1-fea8-47dc-aa2a-7d108fc7ff16
      title: Task One
      start_task: true
      time_period: 4
      steps:
      - key: 3b2e3f35-1a42-41a1-af8b-c40578671ea5
        title: Request Doc
        type: document
      - key: 984c0947-72da-41b5-a0d4-ffb36b31e1ba
        title: Optional Doc
        type: document
        optional: true
        time_period: 2
      - key: ca007aa2-71e0-4c2a-85de-721b3d76cb9a
        title:
          de: Entscheid (de)
          fr: Entscheid (fr)
          it: Entscheid (it)
        type: form
        name: entscheid_form
        fields:
        - name: answer
          type: str
          label:
            de: Antwort (de)
            fr: Antwort (fr)
            it: Antwort (it)
          choices:
          - label:
              de: Ja (de)
              fr: Ja (fr)
              it: Ja (it)
            value: ja
          - label:
              de: Nein (de)
              fr: Nein (fr)
              it: Nein (it)
            value: nein
      links:
      - task_ref: task2
        condition: form.entscheid_form.answer == `"ja"`
      - task_ref: END
      triggers:
      - type: "SampleTrigger"
        value:
          source: "test"
          priority: 1
    task2:
      key: b650c0a9-176b-48b9-9150-86deecf51bdb
      title: Task Two
      steps:
      - key: 90fef4fd-6975-4e17-9b44-b8fb9367d0c3
        title: Another Doc
        type: document
      links:
      - task_ref: END
    END:
      key: c705eed1-10a9-4557-9053-0c34c39124ce
      title: __END__
      links: []
"""


class SampleTrigger(Event):
    pass


def _test_trigger_manager(session: Session) -> WorkflowManager:
    def handler(_session: Session, _event: Event, _node: Node, _ctx: object) -> None:
        pass

    mgr = WorkflowManager(
        session,
        WorkflowManagerConfig(),
    )
    mgr.register_event_handler(SampleTrigger, handler)
    return mgr


def test_load_simple(mgr: WorkflowManager):
    workflow, _ = mgr.load_workflow_from_file(StringIO(SIMPLE_WORKFLOW))
    mgr.session.flush()
    mgr.session.expire_all()

    assert workflow.wf_config_id is not None
    assert workflow.workflow_id == workflow.wf_config_id
    assert workflow.key == UUID("f222bcc9-e563-4014-acb3-f696860f9a0f")
    assert workflow.version == 1
    assert workflow.min_per_entity == 0
    assert workflow.max_per_entity is None

    translations = get_translations(mgr.session, str(workflow.key))

    assert translations[workflow.title]["de"] == "Auskunft (de)"
    assert translations[workflow.title]["fr"] == "Auskunft (fr)"
    assert translations[workflow.title]["it"] == "Auskunft (it)"

    assert workflow.start_task is not None
    assert workflow.start_task.key == UUID("2e310749-c2ed-4c2c-b26a-568a4e61c1d3")

    assert list(workflow.tasks.keys()) == ["anfrage", "END"]

    anfrage = workflow.tasks["anfrage"]
    assert anfrage.is_start_task is True
    assert anfrage.name == "anfrage"
    assert len(anfrage.steps) == 1
    assert anfrage.steps[0].step.type == "document"
    assert len(anfrage.links) == 1


def test_load_extended(session: Session):
    mgr = _test_trigger_manager(session)
    workflow, _ = mgr.load_workflow_from_file(StringIO(EXTENDED_WORKFLOW))
    mgr.session.flush()
    mgr.session.expire_all()

    assert workflow.key == UUID("d253b732-5b06-431c-88d9-e4daa9217819")
    assert workflow.time_period == 14

    task1 = workflow.tasks["task1"]
    assert task1.time_period == 4
    assert task1.is_start_task is True
    assert len(task1.steps) == 3
    assert len(task1.links) == 2
    assert task1.triggers == [
        {"type": "SampleTrigger", "value": {"source": "test", "priority": 1}}
    ]

    # Check form step with fields
    form_step = task1.steps[2].step
    assert isinstance(form_step, Form)
    assert form_step.name == "entscheid_form"
    assert len(form_step.fields) == 1
    assert form_step.fields[0]["name"] == "answer"
    assert len(form_step.fields[0]["choices"]) == 2

    # Check translations for form labels
    translations = get_translations(mgr.session, str(workflow.key))
    label_key = form_step.fields[0]["label"]
    assert translations[label_key]["de"] == "Antwort (de)"
    assert translations[label_key]["fr"] == "Antwort (fr)"

    # Check link conditions
    assert task1.links[0].condition == 'form.entscheid_form.answer == `"ja"`'
    assert task1.links[1].condition is None


def test_load_without_key_generates_uuid(mgr: WorkflowManager):
    yaml_content = """\
workflow:
  title: No Key
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
    workflow, _ = mgr.load_workflow_from_file(StringIO(yaml_content))
    mgr.session.flush()
    mgr.session.expire_all()
    assert workflow.key is not None
    assert workflow.wf_config_id is not None


def test_roundtrip_simple(mgr: WorkflowManager):
    original = yaml.safe_load(StringIO(SIMPLE_WORKFLOW))
    workflow, _ = mgr.load_workflow_from_file(StringIO(SIMPLE_WORKFLOW))
    mgr.session.flush()
    mgr.session.expire_all()

    buffer = StringIO()
    mgr.dump_workflow_to_file(str(workflow.key), buffer)
    buffer.seek(0)
    dumped = yaml.safe_load(buffer)

    assert dumped == original


def test_roundtrip_extended(session: Session):
    mgr = _test_trigger_manager(session)
    original = yaml.safe_load(StringIO(EXTENDED_WORKFLOW))
    workflow, _ = mgr.load_workflow_from_file(StringIO(EXTENDED_WORKFLOW))
    mgr.session.flush()
    mgr.session.expire_all()

    buffer = StringIO()
    mgr.dump_workflow_to_file(str(workflow.key), buffer)
    buffer.seek(0)
    dumped = yaml.safe_load(buffer)

    assert dumped == original


def test_load_unregistered_event_trigger_raises(mgr: WorkflowManager):
    with pytest.raises(EventHandlerNotRegistered, match="SampleTrigger"):
        mgr.load_workflow_from_file(StringIO(EXTENDED_WORKFLOW))


def test_load_readme_example(mgr: WorkflowManager):
    """The example YAML from the README can be loaded successfully."""
    yaml_text = _extract_readme_workflow()
    workflow, _ = mgr.load_workflow_from_file(StringIO(yaml_text))
    mgr.session.flush()
    mgr.session.expire_all()

    assert workflow.wf_config_id is not None
    assert workflow.version == 1
    assert workflow.min_per_entity == 0
    assert workflow.max_per_entity == 1

    assert set(workflow.tasks.keys()) == {"review", "finalize", "reject", "END"}

    review = workflow.tasks["review"]
    assert review.is_start_task is True
    assert len(review.steps) == 2
    assert review.steps[0].step.type == "form"
    assert review.steps[1].step.type == "document"
    assert len(review.links) == 2

    finalize = workflow.tasks["finalize"]
    assert len(finalize.steps) == 1
    assert len(finalize.links) == 1

    reject = workflow.tasks["reject"]
    assert len(reject.steps) == 1
    assert len(reject.links) == 1
