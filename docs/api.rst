Workflow Engine
===============

.. module:: business_workflow_manager

WorkflowManager
---------------

.. autoclass:: WorkflowManager
   :members:
   :undoc-members:

.. autoclass:: WorkflowManagerConfig
   :members:

.. autofunction:: manager_factory

Data Transfer Objects
---------------------

.. autoclass:: NodeInfo()
   :members:
   :undoc-members:

.. autoclass:: WorkflowNodeInfo()
   :members:
   :undoc-members:
   :show-inheritance:

.. autoclass:: TaskNodeInfo()
   :members:
   :undoc-members:
   :show-inheritance:

.. autoclass:: FormNodeInfo()
   :members:
   :undoc-members:
   :show-inheritance:

.. autoclass:: DocumentNodeInfo()
   :members:
   :undoc-members:
   :show-inheritance:

.. autoclass:: NoteNodeInfo()
   :members:
   :undoc-members:
   :show-inheritance:

.. autoclass:: StepOption()
   :members:
   :undoc-members:

Events
------

.. autoclass:: Event
   :members:
   :undoc-members:

.. data:: EventHandler

   Type alias: ``Callable[[Session, Event], None]``

.. data:: EventHandlerMap

   Type alias: ``dict[type[Event], list[EventHandler]]``
