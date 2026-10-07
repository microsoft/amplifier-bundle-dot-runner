"""Public tool correlations are timed; cumulative turns aren't LLM calls."""

import json
import tempfile
import unittest
from pathlib import Path

from node_timing_table import SessionStats, build_report


class PublicTiming(unittest.TestCase):
    def test_correlated_overlap_and_unknown_llm_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = root / "worker" / "sessions" / "actual"
            stage.mkdir(parents=True)
            events = []
            for kind, at, payload in [
                ("turn_started", "00", {}),
                ("tool_call", "01", {"call": {"call_id": "a", "name": "read_file"}}),
                ("tool_call", "02", {"call": {"call_id": "b", "name": "write_file"}}),
                ("tool_result", "04", {"resolution": {"call_id": "a"}}),
                ("tool_result", "08", {"resolution": {"call_id": "b"}}),
                ("usage", "09", {"snapshot": {"entries": []}}),
                ("terminal", "59", {}),
            ]:
                events.append(
                    {
                        "event": f"amplifier-agent:{kind}",
                        "timestamp": f"2026-01-01T00:00:{at}+00:00",
                        "data": {
                            "contract_version": "turn-events/1",
                            "session_id": "actual",
                            "turn_id": "t",
                            "type": kind,
                            "payload": payload,
                        },
                    }
                )
            path = stage / "events.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in events))
            stats = SessionStats()
            stats.ingest(path)
            self.assertEqual(stats.tool_calls, 2)
            self.assertEqual(stats.longest_seconds, 6)
            self.assertEqual(stats.longest_label, "tool (write_file)")
            (root / "trace.jsonl").write_text(
                json.dumps(
                    {
                        "node_id": "worker",
                        "iteration": 0,
                        "duration_ms": 59000,
                        "status": "success",
                    }
                )
            )
            report, _ = build_report(root)
            self.assertIn("| - | 2 | 6.0s tool (write_file)", report)


if __name__ == "__main__":
    unittest.main()
