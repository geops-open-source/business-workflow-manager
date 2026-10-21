"""Tests for business_workflow_manager.loader (load/dump workflow YAML ↔ DB)."""

import re
from datetime import datetime
from io import StringIO
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from business_workflow_manager import (
    Event,
    EventHandlerNotRegistered,
    WorkflowManager,
    WorkflowManagerConfig,
    WorkflowSelectionError,
)
from business_workflow_manager.config import Workflow
from business_workflow_manager.loader import get_translations
from business_workflow_manager.models import Form, Node, Translation, WorkflowConfig
from business_workflow_manager.types import Language

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


# --- Selection import (ALMABASE-710) ---

SIMPLE_KEY = "f222bcc9-e563-4014-acb3-f696860f9a0f"
SIMPLE_TITLE_KEY = f"wf:{SIMPLE_KEY}:title"
SIMPLE_STEP_KEY = f"wf:{SIMPLE_KEY}:task:anfrage:step:1:title"


def _translation_rows(
    session: Session, prefix: str
) -> dict[tuple[str, str], tuple[str, datetime | None]]:
    rows = session.execute(
        select(
            Translation.key,
            Translation.locale,
            Translation.value,
            Translation.updated_at,
        ).where(Translation.key.startswith(prefix))
    )
    return {(k, str(loc)): (v, u) for k, loc, v, u in rows}


def _mark_edited(session: Session, key: str, locale: Language, value: str) -> None:
    session.execute(
        update(Translation)
        .where(Translation.key == key, Translation.locale == locale)
        .values(value=value, updated_at=datetime(2026, 1, 1))
    )


def _setup(tmp_path: Path, files: dict[str, str], selection: str) -> tuple[Path, Path]:
    base = tmp_path / "workflows"
    base.mkdir()
    for name, content in files.items():
        (base / name).write_text(content)
    sel = tmp_path / "workflows.txt"
    sel.write_text(selection)
    return sel, base


def _import(mgr: WorkflowManager, sel: Path, base: Path):
    result = mgr.load_workflows_from_selection(sel, base)
    mgr.session.flush()
    mgr.session.expire_all()
    return result


def _count(session: Session) -> int | None:
    return session.scalar(select(func.count()).select_from(WorkflowConfig))


def test_selection_import_is_idempotent(mgr: WorkflowManager, tmp_path: Path):
    sel, base = _setup(tmp_path, {"simple.yml": SIMPLE_WORKFLOW}, "simple.yml\n")

    first = _import(mgr, sel, base)
    before = _translation_rows(mgr.session, f"wf:{SIMPLE_KEY}")
    count = _count(mgr.session)
    second = _import(mgr, sel, base)

    assert [str(w.key) for w in first] == [SIMPLE_KEY]
    assert first[0].wf_config_id == second[0].wf_config_id
    assert _translation_rows(mgr.session, f"wf:{SIMPLE_KEY}") == before
    assert _count(mgr.session) == count
    assert all(u is None for _, u in before.values())


def test_selection_keeps_edited_translation(mgr: WorkflowManager, tmp_path: Path):
    sel, base = _setup(tmp_path, {"simple.yml": SIMPLE_WORKFLOW}, "simple.yml\n")
    _import(mgr, sel, base)
    _mark_edited(mgr.session, SIMPLE_TITLE_KEY, Language.DE, "Edited")
    stale_key = f"wf:{SIMPLE_KEY}:task:old:title"
    mgr.session.add(Translation(key=stale_key, value="Old", locale=Language.DE))
    mgr.session.flush()
    mgr.session.execute(delete(Translation).where(Translation.key == SIMPLE_STEP_KEY))

    (base / "simple.yml").write_text(SIMPLE_WORKFLOW.replace("Auskunft", "Neu"))
    _import(mgr, sel, base)

    rows = _translation_rows(mgr.session, f"wf:{SIMPLE_KEY}")
    assert rows[(SIMPLE_TITLE_KEY, "de")] == ("Edited", datetime(2026, 1, 1))
    assert rows[(SIMPLE_TITLE_KEY, "fr")] == ("Neu (fr)", None)
    assert rows[(SIMPLE_STEP_KEY, "de")] == ("Anfrage Dokument", None)
    assert (stale_key, "de") in rows


def _load_file(mgr: WorkflowManager, content: str) -> None:
    mgr.load_workflow_from_file(StringIO(content))
    mgr.session.flush()
    mgr.session.expire_all()


def test_load_workflow_from_file_keeps_edited_translation(mgr: WorkflowManager):
    _load_file(mgr, SIMPLE_WORKFLOW)
    _mark_edited(mgr.session, SIMPLE_TITLE_KEY, Language.DE, "Edited")
    stale_key = f"wf:{SIMPLE_KEY}:task:old:title"
    mgr.session.add(Translation(key=stale_key, value="Old", locale=Language.DE))
    mgr.session.flush()
    mgr.session.execute(delete(Translation).where(Translation.key == SIMPLE_STEP_KEY))

    _load_file(mgr, SIMPLE_WORKFLOW.replace("Auskunft", "Neu"))

    rows = _translation_rows(mgr.session, f"wf:{SIMPLE_KEY}")
    assert rows[(SIMPLE_TITLE_KEY, "de")] == ("Edited", datetime(2026, 1, 1))
    assert rows[(SIMPLE_TITLE_KEY, "fr")] == ("Neu (fr)", None)
    assert rows[(SIMPLE_STEP_KEY, "de")] == ("Anfrage Dokument", None)
    assert (stale_key, "de") in rows


def test_load_workflow_keeps_edited_translation(mgr: WorkflowManager):
    _load_file(mgr, SIMPLE_WORKFLOW)
    _mark_edited(mgr.session, SIMPLE_TITLE_KEY, Language.DE, "Edited")
    mgr.load_workflow(Workflow.read(StringIO(SIMPLE_WORKFLOW)))
    mgr.session.flush()
    mgr.session.expire_all()
    rows = _translation_rows(mgr.session, SIMPLE_TITLE_KEY)
    assert rows[(SIMPLE_TITLE_KEY, "de")] == ("Edited", datetime(2026, 1, 1))


def test_load_workflow_from_file_is_idempotent(mgr: WorkflowManager):
    _load_file(mgr, SIMPLE_WORKFLOW)
    before = _translation_rows(mgr.session, f"wf:{SIMPLE_KEY}")
    _load_file(mgr, SIMPLE_WORKFLOW)
    assert _translation_rows(mgr.session, f"wf:{SIMPLE_KEY}") == before
    assert all(u is None for _, u in before.values())


def test_selection_missing_file(mgr: WorkflowManager, tmp_path: Path):
    with pytest.raises(WorkflowSelectionError, match="Cannot read"):
        mgr.load_workflows_from_selection(tmp_path / "nope.txt", tmp_path)


def test_selection_missing_listed_file(mgr: WorkflowManager, tmp_path: Path):
    sel, base = _setup(
        tmp_path, {"simple.yml": SIMPLE_WORKFLOW}, "simple.yml\nmissing.yml\n"
    )
    with pytest.raises(WorkflowSelectionError, match="missing.yml"):
        mgr.load_workflows_from_selection(sel, base)
    assert _count(mgr.session) == 0


def test_selection_error_in_second_file_writes_nothing(
    mgr: WorkflowManager, tmp_path: Path
):
    sel, base = _setup(
        tmp_path,
        {"a.yml": SIMPLE_WORKFLOW, "b.yml": "workflow: {broken: 1}\n"},
        "a.yml\nb.yml\n",
    )
    with pytest.raises(WorkflowSelectionError, match="b.yml"):
        mgr.load_workflows_from_selection(sel, base)
    assert _translation_rows(mgr.session, f"wf:{SIMPLE_KEY}") == {}
    assert _count(mgr.session) == 0
