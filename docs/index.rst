=========================
business-workflow-manager
=========================

A reusable workflow engine for Python applications backed by SQLAlchemy ORM
models. Define multi-step workflows in YAML, load them into a relational
database, and drive execution at runtime with condition-based branching.

.. toctree::
   :maxdepth: 2
   :caption: API Reference:

   api
   reference

Features
========

- **YAML-based workflow definitions** — version-controlled, human-readable
  workflow configurations
- **SQLAlchemy ORM models** — ready-to-use Single Table Inheritance model
  hierarchy
- **Runtime engine** — step progression with jmespath condition evaluation
- **Multilingual** — built-in support for DE/FR/IT translated strings
- **GraphViz export** — generate DOT diagrams from workflow configs
- **Session-friendly** — works with your existing SQLAlchemy session; never
  commits

Installation
============

.. code-block:: bash

   pip install business-workflow-manager

Or with `uv <https://docs.astral.sh/uv/>`_:

.. code-block:: bash

   uv add business-workflow-manager

Dependencies
------------

- Python ≥ 3.11
- SQLAlchemy ~= 2.0
- Pydantic ≥ 2.0
- PyYAML
- jmespath

Quick Start
===========

1. Define a Workflow in YAML
----------------------------

.. code-block:: yaml

   workflow:
     title:
       de: Bewilligungsverfahren
       fr: Procédure d'autorisation
       it: Procedura di autorizzazione
     version: 1
     start_task_ref: review
     min_per_entity: 0
     max_per_entity: 1
     tasks:
       review:
         title: Review
         start_task: true
         steps:
           - type: form
             title: Assessment
             name: assessment
             fields:
               - name: approved
                 type: boolean
                 label: Approved?
           - type: document
             title: Upload Decision
         links:
           - task_ref: finalize
             condition: "approved == `true`"
           - task_ref: reject
             condition: "approved == `false`"
       finalize:
         title: Finalize
         steps:
           - type: document
             title: Final Document
         links:
           - task_ref: END
       reject:
         title: Reject
         steps:
           - type: document
             title: Rejection Letter
         links:
           - task_ref: END
       END:
         title: __END__
         steps: []
         links: []

2. Set Up the Database
----------------------

The library ships its own ``DeclarativeBase``. Create its tables alongside your
application tables:

.. code-block:: python

   from sqlalchemy import create_engine
   from business_workflow_manager.models import Base

   engine = create_engine("postgresql://localhost/mydb")

   # Create workflow tables (wf_config, wf_node, wf_link, wf_step, translations)
   Base.metadata.create_all(engine)

3. Load a Workflow into the Database
------------------------------------

.. code-block:: python

   from sqlalchemy.orm import Session
   from business_workflow_manager import WorkflowManager

   with Session(engine) as session:
       with WorkflowManager(session) as mgr:
           with open("workflows/review.yaml") as f:
               workflow, config = mgr.load_workflow_from_file(f)
           session.commit()  # The library flushes but never commits — you control transactions

4. Start a Workflow Instance
----------------------------

.. code-block:: python

   from business_workflow_manager import WorkflowManager

   with Session(engine) as session:
       with WorkflowManager(session) as mgr:

       # Create a workflow instance for entity "42"
       # (checks applicability, raises if max_per_entity exceeded)
       wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="42")
       # => WorkflowNodeInfo(wf_node_id=1, status=STARTED, ...)

       # Get available first steps (start tasks)
       next_steps = mgr.get_next_steps(wf_node.wf_node_id)
       # => [StepOption(wf_config_id=5, name='review', type=TASK, ...)]

       # Start the first task
       task_node = mgr.start_next_step(wf_node.wf_node_id, next_steps[0].wf_config_id)
       # => TaskNodeInfo(wf_node_id=2, status=STARTED, ...)

       # Get the task's steps
       steps = mgr.get_next_steps(task_node.wf_node_id)
       # => [StepOption(wf_config_id=8, name='assessment', type=FORM, ...)]

       # Start the form step
       form_node = mgr.start_next_step(task_node.wf_node_id, steps[0].wf_config_id)
       # => FormNodeInfo(wf_node_id=3, status=STARTED, ...)

       session.commit()

All public API methods return **frozen dataclasses** (``NodeInfo`` subclasses
and ``StepOption``), not mutable ORM instances. Methods accept plain integer IDs
(``node_id``, ``step_id``) as arguments.

5. Conditional Branching
------------------------

After completing all steps in a task, the engine evaluates link conditions to
determine which task(s) come next:

.. code-block:: python

   # After finishing all steps in the "review" task, get next tasks:
   next_tasks = mgr.get_next_steps(task_node.wf_node_id)
   # If approved==True: => [StepOption(name='finalize', ...)]
   # If approved==False: => [StepOption(name='reject', ...)]

Conditions use `jmespath <https://jmespath.org/>`_ expressions evaluated against
``node.data``. Use backtick-quoted JSON literals for comparison values
(```true```, ```false```, ```"string"```).

6. Updating a Workflow Node
---------------------------

.. code-block:: python

   from datetime import datetime
   from business_workflow_manager import WorkflowManager, CascadeConflict, NodeStatus

   with Session(engine) as session:
       mgr = WorkflowManager(session)

       # Update fields on a workflow node
       updated = mgr.update_workflow_node(
           wf_node.wf_node_id,
           title="Renamed workflow",
           deadline=datetime(2026, 6, 1),
           status=NodeStatus.FINISHED,
           cascade=False,  # default: raise if children conflict
       )

       # If children have later deadlines or are still open, CascadeConflict is raised:
       try:
           mgr.update_workflow_node(node_id, status=NodeStatus.FINISHED)
       except CascadeConflict as e:
           print(f"Conflicting children: {e.status_conflicts}")
           # Ask user to confirm, then retry with cascade=True
           mgr.update_workflow_node(node_id, status=NodeStatus.FINISHED, cascade=True)

       session.commit()

Status transitions have side effects:

- **STARTED**: clears ``finished_at``
- **INACTIVE**: clears both ``deadline`` and ``finished_at``

``update_task_node`` works the same way but cascades **upward** — it checks
parent/grandparent nodes:

- If the task's deadline exceeds a parent's deadline → conflict
- If setting the task to non-finished while a parent is already finished →
  conflict

With ``cascade=True``, parent deadlines are extended and finished parents are
reopened.

7. Creating Ad-hoc Documents
-----------------------------

Documents can be created independently of a workflow step definition (no config
required):

.. code-block:: python

   from datetime import datetime
   from business_workflow_manager import WorkflowManager

   with Session(engine) as session:
       mgr = WorkflowManager(session)

       doc = mgr.create_document_node(
           entity_id="42",
           title="Contract signed",
           started_at=datetime(2024, 6, 15),
           document_ref="DOC-2024-001",
           note="Original contract",
           is_public=True,
           parent_id=task_node.wf_node_id,  # optional: attach under a task or workflow
       )
       # => DocumentNodeInfo(wf_node_id=..., status=FINISHED, document_ref="DOC-2024-001")

       session.commit()

Ad-hoc documents are always created with ``status=FINISHED`` and all timestamps
(``started_at``, ``finished_at``, ``deadline``) set to the same value. If
``parent_id`` is provided, the method walks up from that node to find the
nearest ``TaskNode`` (preferred) or ``WorkflowNode`` ancestor. If no suitable
parent is found, the document is created at the top level. This means creation
never fails due to parent resolution.

``create_note_node`` works the same way but without a ``document_ref`` field:

.. code-block:: python

   note = mgr.create_note_node(
       entity_id="42",
       title="Follow-up required",
       started_at=datetime(2024, 6, 15),
       note="Discuss with legal team",
       parent_id=task_node.wf_node_id,
   )
   # => NoteNodeInfo(wf_node_id=..., status=FINISHED, ...)

8. Event Handling
-----------------

Define domain events by subclassing ``Event``, register handlers at startup,
and dispatch them through the manager:

.. code-block:: python

   from sqlalchemy.orm import Session
   from business_workflow_manager import Event, WorkflowManagerConfig, WorkflowManager, manager_factory


   # Define your events
   class InvoiceApproved(Event):
       def __init__(self, invoice_id: int) -> None:
           self.invoice_id = invoice_id


   # Define handlers — receive the session and event
   def notify_accounting(session: Session, event: Event) -> None:
       assert isinstance(event, InvoiceApproved)
       # ... use session to query/update the database ...


   # Configure once at startup
   def get_entity_data(session, entity_id):
       # Fetch and return a JSON-serializable dict for the entity
       return {"id": entity_id, "name": "Example"}

   config = WorkflowManagerConfig(get_entity_data=get_entity_data)

   # Option A: pass config directly
   with WorkflowManager(session, config) as mgr:
       mgr.register_event_handler(InvoiceApproved, notify_accounting)
       mgr.handle_event(InvoiceApproved(invoice_id=123))

   # Option B: use a factory (bind config once, create managers with just a session)
   new_manager = manager_factory(config)
   with new_manager(session) as mgr:
       mgr.register_event_handler(InvoiceApproved, notify_accounting)
       mgr.handle_event(InvoiceApproved(invoice_id=123))

Handlers are called in registration order with the active session and event
instance.

Integration Guide
=================

Using with an Existing Application
-----------------------------------

The library uses its own ``DeclarativeBase``. If your application has its own
base, you have two options:

**Option A: Shared metadata (recommended)**

Map the workflow tables into your application's metadata:

.. code-block:: python

   from business_workflow_manager.models import Base as WorkflowBase
   from myapp.models import Base as AppBase

   # Point workflow models at your app's metadata
   WorkflowBase.metadata = AppBase.metadata

**Option B: Separate metadata**

Keep them separate and create tables from both:

.. code-block:: python

   AppBase.metadata.create_all(engine)
   WorkflowBase.metadata.create_all(engine)

Entity ID
---------

The ``Node.entity_id`` column is a plain string with no foreign key constraint.
Your application is responsible for ensuring it references a valid entity. This
keeps the library decoupled from your domain model.

.. code-block:: python

   # In your app, create workflow instances tied to your entities:
   wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id=str(my_entity.id))

Transaction Management
----------------------

The library **never calls** ``session.commit()``. It uses ``session.flush()`` to
obtain generated primary keys but leaves transaction boundaries entirely to you:

.. code-block:: python

   with Session(engine) as session:
       mgr = WorkflowManager(session)
       workflow = mgr.load_workflow(config)
       # ... do other work in the same transaction ...
       session.commit()  # You decide when to commit

YAML Schema Reference
=====================

.. code-block:: yaml

   workflow:
     key: <uuid>                    # Optional. Stable ID for updates.
     title: <string | translated>   # Workflow title
     version: <int>                 # Schema version
     start_task_ref: <task_ref>     # Reference to the default initial task
     min_per_entity: <int>          # Minimum instances per entity
     max_per_entity: <int | null>   # Maximum instances (null = unlimited)
     time_period: <int | null>      # Default deadline in days
     tasks:
       <task_ref>:                   # String key used for linking
         key: <uuid>                # Optional. Stable ID for updates.
         title: <string | translated>
         start_task: <bool>         # If true, the workflow can alternatively be started at this task
         time_period: <int | null>
         triggers:                  # Optional event triggers
           - type: <string>         # Must match a registered event class name
             value: <object>        # Mapping → event(**value); scalar → event(value); omit → event()
         steps:
           - type: document
             key: <uuid>            # Optional
             title: <string | translated>
             optional: <bool>       # Default: false
             condition: <jmespath>  # Optional, skip if false
             time_period: <int | null>
           - type: form
             key: <uuid>
             title: <string | translated>
             name: <string>         # Form identifier
             optional: <bool>
             condition: <jmespath>
             time_period: <int | null>
             fields:
               - name: <string>
                 type: <string>     # e.g. "boolean", "text", "select"
                 label: <string | translated>
                 choices:           # Optional, for select fields
                   - label: <string | translated>
                     value: <string>
         links:
           - task_ref: <task_ref>   # Target task
             condition: <jmespath>  # Optional, follow if true

**Translated strings** can be either a plain string (used for all languages) or
an object with ``de``, ``fr``, ``it`` keys.

Development
===========

.. code-block:: bash

   # Clone and install
   git clone <repo-url>
   cd business-workflow-manager
   uv sync --dev

   # Run tests
   uv run pytest

   # Run tests with verbose output
   uv run pytest -v

License
=======

GNU Lesser General Public License v3.0 only (``LGPL-3.0-only``) — see the
``LICENSE`` file (LGPL) and the ``COPYING`` file (GPL, which the LGPL builds on)
for details.
