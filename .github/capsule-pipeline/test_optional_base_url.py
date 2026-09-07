"""`OPENAI_BASE_URL` is optional -- and an empty one must not shred evidence.

Owner ruling 2026-09-07: the endpoint is OPTIONAL configuration (an Actions
variable), not a credential.  The workflows still name it in
``SCRUB_WATCH_ENV`` so layer 3 redacts its literal value wherever it turns
up in run evidence -- and on a run where nobody set it, that "value" is the
empty string.

An empty literal is the dangerous case, not a harmless one: the empty
string matches at every position, so a watcher that took it would either
redact between every byte of every artifact or blow the upload gate on
every file.  `_watched_literals`'s ``MIN_LITERAL_LEN`` floor already
refuses it; nothing pinned that, and nothing said WHY it mattered.  This
does both.

Stdlib only, like every other test in this directory (the
`capsule-pipeline-scripts` CI job ships no dependencies).  Deliberately a
separate file: `scrub_secrets.py` and `test_scrub_secrets.py` are vendored
byte-identical from microsoft/amplifier-bundle-attractor and must not be
hand-edited here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import scrub_secrets  # noqa: E402  (path juggling above is deliberate)

ENDPOINT = "https://endpoint.invalid/v1/openai"


class EmptyWatchedValueIsIgnored(unittest.TestCase):
    def _literals(self, base_url: str) -> dict[str, str]:
        env = {"SCRUB_WATCH_ENV": "OPENAI_BASE_URL", "OPENAI_BASE_URL": base_url}
        with mock.patch.dict(scrub_secrets.os.environ, env, clear=True):
            return scrub_secrets._watched_literals()

    def test_unset_endpoint_contributes_no_literal(self) -> None:
        self.assertEqual(
            self._literals(""),
            {},
            "An empty OPENAI_BASE_URL became a watched literal. The empty "
            "string matches everywhere, so every artifact would be redacted "
            "into nothing (or fail the upload gate). Unset is the normal case "
            "now -- the endpoint is optional (owner ruling 2026-09-07).",
        )

    def test_a_real_endpoint_is_still_watched(self) -> None:
        """The other direction: making it optional must not make it unwatched."""
        self.assertEqual(self._literals(ENDPOINT), {"OPENAI_BASE_URL": ENDPOINT})

    def test_evidence_survives_an_unset_endpoint_untouched(self) -> None:
        evidence = 'node=critique provider=luna base_url="" status=ok\n'
        scrubbed, shapes = scrub_secrets.scrub_text(evidence, self._literals(""))
        self.assertEqual(scrubbed, evidence)
        self.assertEqual(shapes, [])

    def test_a_real_endpoint_is_still_redacted_out_of_evidence(self) -> None:
        evidence = f"node=critique base_url={ENDPOINT} status=ok\n"
        scrubbed, shapes = scrub_secrets.scrub_text(evidence, self._literals(ENDPOINT))
        self.assertNotIn(ENDPOINT, scrubbed)
        self.assertIn("env:OPENAI_BASE_URL", shapes)


if __name__ == "__main__":
    unittest.main()
