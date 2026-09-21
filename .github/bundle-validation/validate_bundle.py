"""Validate the repository's bundle files without a model or CLI session."""

import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from amplifier_foundation import BundleRegistry, load_bundle
from amplifier_foundation.validator import BundleValidator


async def validate_repository(root: Path) -> dict:
    root = root.resolve()
    manifest = root / "bundle.md"
    behaviors = sorted(
        path
        for path in (root / "behaviors").rglob("*")
        if path.is_file() and path.suffix in {".yaml", ".yml"}
    )
    errors = []
    if not manifest.is_file():
        errors.append("Missing root bundle.md")
    if not behaviors:
        errors.append("No behavior manifests found")
    results = []
    with TemporaryDirectory(prefix="bundle-validation-") as directory:
        registry = BundleRegistry(Path(directory), strict=True)
        for path in ([manifest] if manifest.is_file() else []) + behaviors:
            relative = str(path.relative_to(root))
            try:
                # Match the recipe's per-file structural check. Loading includes
                # would validate remote main rather than this checkout's files.
                bundle = await load_bundle(
                    path.as_uri(), auto_include=False, registry=registry
                )
                result = BundleValidator().validate(bundle)
                results.append(
                    {
                        "path": relative,
                        "name": bundle.name,
                        "errors": result.errors,
                        "warnings": result.warnings,
                    }
                )
                errors.extend(f"{relative}: {error}" for error in result.errors)
            except Exception as error:
                errors.append(f"{relative}: {type(error).__name__}: {error}")
    return {"ok": not errors, "bundles": results, "errors": errors}


if __name__ == "__main__":
    report = asyncio.run(validate_repository(Path(sys.argv[1])))
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["ok"] else 1)
