"""Shared constant for backend/api/routes/* submodules.

Kept in its own module rather than backend/api/routes/__init__.py: every
submodule needs TEST_PORT_INTERFACE, and __init__.py needs to import
every submodule to assemble the combined router -- putting the constant
there too would make each submodule's `from backend.api.routes import
TEST_PORT_INTERFACE` a circular import (the package's own __init__ not
yet finished executing). Importing from this leaf module instead avoids
the cycle entirely.
"""

TEST_PORT_INTERFACE = "eth0"
