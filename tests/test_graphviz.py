"""Tests for business_workflow_manager.graphviz (DOT export)."""

from io import StringIO

from business_workflow_manager.config import Workflow
from business_workflow_manager.graphviz import write_graph
from business_workflow_manager.types import Language

WORKFLOW_YAML = """\
workflow:
  title:
    de: Workflow DE
    fr: Workflow FR
    it: Workflow IT
  version: 1
  start_task_ref: task1
  min_per_entity: 0
  max_per_entity: null
  tasks:
    task1:
      title:
        de: Aufgabe 1
        fr: Tâche 1
        it: Compito 1
      steps:
      - title: Dokument
        type: document
      - title: Formular
        type: form
        name: f1
        fields: []
        condition: x == `1`
      links:
      - task_ref: END
        condition: y.z == `"done"`
    END:
      title: __END__
      steps: []
      links: []
"""


def test_generates_dot_de():
    config = Workflow.read(StringIO(WORKFLOW_YAML))
    output = StringIO()
    write_graph(config, output, Language.DE)
    dot = output.getvalue()

    assert "digraph {" in dot
    assert "Workflow DE" in dot
    assert "Aufgabe 1" in dot
    assert "[D] Dokument" in dot
    assert "[F] Formular *" in dot  # has condition marker
    assert "task1 -> END" in dot
    assert 'y\\n.z == `"done"`' in dot


def test_generates_dot_fr():
    config = Workflow.read(StringIO(WORKFLOW_YAML))
    output = StringIO()
    write_graph(config, output, Language.FR)
    dot = output.getvalue()

    assert "Workflow FR" in dot
    assert "Tâche 1" in dot


def test_generates_dot_it():
    config = Workflow.read(StringIO(WORKFLOW_YAML))
    output = StringIO()
    write_graph(config, output, Language.IT)
    dot = output.getvalue()

    assert "Workflow IT" in dot
    assert "Compito 1" in dot


def test_start_node():
    config = Workflow.read(StringIO(WORKFLOW_YAML))
    output = StringIO()
    write_graph(config, output, Language.DE)
    dot = output.getvalue()

    assert "__START__" in dot
    assert "__START__ -> task1" in dot
