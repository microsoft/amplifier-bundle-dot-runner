## Summary

<!-- What changed and why. -->

## Verification

<!-- Tests run, RED-proofs, ruff/lint output, diff scope. -->

## Checklist

- [ ] nlspec evidence: cited section(s) + holistic-context note (spec silence ≠ support; silence → extension process or design guidance)
- [ ] Tests pass (`uv run pytest -q` in each touched module)
- [ ] `ruff check` / `ruff format --check` clean
- [ ] Diff scope matches the description above (no unrelated files)
- [ ] Touching `specs/EXTENSIONS.md` or `specs/conformance/`? The change is deliberate and described above
