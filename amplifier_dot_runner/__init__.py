"""Thin root package for the no-#subdirectory install form.

``uv tool install git+https://github.com/microsoft/amplifier-bundle-dot-runner``
(no ``#subdirectory=...`` pin) resolves THIS package as the repo root's
distribution. It carries no logic of its own -- it depends on
``amplifier-module-pipeline-runner`` (see ``pyproject.toml``'s
``[tool.uv.sources]``, a relative-path source pointing at
``modules/pipeline-runner``) and simply re-exports its ``dot-runner`` CLI
entry point, so the root install form and the existing
``#subdirectory=modules/pipeline-runner`` install form both land exactly one
console script: ``dot-runner``. Module packages keep their own names and
pyproject.toml files intact -- this is purely an additional, thin
distribution root; nothing about ``modules/pipeline-runner``'s own
subdirectory-pinned install changed.
"""

from __future__ import annotations

from amplifier_module_pipeline_runner.cli import main

__all__ = ["main"]
