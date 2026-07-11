"""Compatibility shim for tool registry discovery.

The autoresearch implementation lives in ``autoresearch.autoresearch_tool``.
This thin module remains under ``tools/`` because ``ToolRegistry.reload_all()``
discovers tool registrations by importing every module in this directory.

Important: ``registry.reload_all()`` clears the registry before reloading
``tools.*`` modules.  If the real module is already cached, a plain import would
not re-run its ``registry.register(...)`` calls.  Reload it explicitly here.
"""

from importlib import import_module, reload

_impl = reload(import_module("autoresearch.autoresearch_tool"))

for _name in dir(_impl):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_impl, _name)

