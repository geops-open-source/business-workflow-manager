"""Tests for business_workflow_manager.config (pydantic YAML schema)."""

from io import StringIO
from uuid import uuid4

import pytest

from business_workflow_manager.config import (
    Document,
    Form,
    Task,
    TranslatedString,
    ValidationError,
    Workflow,
)


@pytest.fixture
def config() -> Workflow:
    return Workflow(
        title="test",
        version=1,
        start_task_ref="task1",
        min_per_entity=0,
        max_per_entity=None,
        tasks={"task1": Task(title="test", steps=[], links=[], triggers=[])},
    )


def test_validate_keys_no_keys(config: Workflow):
    config.validate_keys()


def test_validate_keys_only_workflow_has_key(config: Workflow):
    config.key = uuid4()
    config.validate_keys()


def test_validate_keys_both_have_key(config: Workflow):
    config.key = uuid4()
    config.tasks["task1"].key = uuid4()
    config.validate_keys()


def test_validate_keys_only_task_has_key(config: Workflow):
    config.tasks["task1"].key = uuid4()
    with pytest.raises(
        ValidationError, match="Task task1 of process 'test' must not have a key"
    ):
        config.validate_keys()


def test_validate_keys_only_process_and_step_have_keys(config: Workflow):
    config.key = uuid4()
    config.tasks["task1"].steps.append(
        Document(
            key=uuid4(),
            title="document1",
            optional=False,
            condition=None,
            type="document",
        )
    )
    with pytest.raises(
        ValidationError,
        match=r"Step 'document1' \(document\) of task 'test' must not have a key",
    ):
        config.validate_keys()


def test_read_simple_workflow():
    yaml_content = """\
workflow:
  title: Simple
  version: 1
  start_task_ref: task1
  min_per_entity: 0
  max_per_entity: null
  tasks:
    task1:
      title: Task 1
      steps:
      - title: Doc 1
        type: document
      links: []
"""
    config = Workflow.read(StringIO(yaml_content))
    assert config.title == "Simple"
    assert config.version == 1
    assert "task1" in config.tasks
    assert len(config.tasks["task1"].steps) == 1
    assert config.tasks["task1"].steps[0].type == "document"


def test_read_missing_workflow_key():
    yaml_content = "not_workflow:\n  title: test\n"
    with pytest.raises(ValueError, match="Top-level key 'workflow' not found"):
        Workflow.read(StringIO(yaml_content))


def test_roundtrip():
    yaml_content = """\
workflow:
  title: Roundtrip
  version: 2
  start_task_ref: t1
  min_per_entity: 1
  max_per_entity: 3
  tasks:
    t1:
      title: Task One
      steps:
      - title: Doc
        type: document
      - title: My Form
        type: form
        name: my_form
        fields:
        - name: field1
          type: str
      links:
      - task_ref: END
    END:
      title: __END__
      steps: []
      links: []
"""
    config = Workflow.read(StringIO(yaml_content))
    output = StringIO()
    config.write(output)
    output.seek(0)
    config2 = Workflow.read(output)
    assert config2.title == config.title
    assert config2.version == config.version
    assert list(config2.tasks.keys()) == list(config.tasks.keys())


def test_translated_strings():
    yaml_content = """\
workflow:
  title:
    de: Titel
    fr: Titre
    it: Titolo
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
    config = Workflow.read(StringIO(yaml_content))
    assert isinstance(config.title, TranslatedString)
    assert config.title.de == "Titel"
    assert config.title.fr == "Titre"
    assert config.title.it == "Titolo"


def test_form_with_choices():
    yaml_content = """\
workflow:
  title: Test
  version: 1
  start_task_ref: t1
  min_per_entity: 0
  max_per_entity: null
  tasks:
    t1:
      title: Task
      steps:
      - title: Decision
        type: form
        name: decision_form
        fields:
        - name: choice_field
          type: str
          label: Pick one
          choices:
          - label: Option A
            value: a
          - label: Option B
            value: b
      links: []
"""
    config = Workflow.read(StringIO(yaml_content))
    form = config.tasks["t1"].steps[0]
    assert isinstance(form, Form)
    assert form.name == "decision_form"
    assert len(form.fields) == 1
    assert form.fields[0].choices is not None
    assert len(form.fields[0].choices) == 2
    assert form.fields[0].choices[0].value == "a"
