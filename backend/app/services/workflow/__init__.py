"""
Importing this package registers every node executor against
NodeExecutorRegistry (decorator side effect) - same "import for
registration side effects" pattern `app/services/monitoring/__init__.py`
uses for `app/collectors/`. `app/main.py` imports this package once at
startup.
"""
from app.services.workflow import node_executors  # noqa: F401
