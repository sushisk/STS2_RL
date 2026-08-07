from __future__ import annotations

import unittest

from API.validation import RequestRejected, validate_request


class SimulationOptionsValidationTest(unittest.TestCase):
    @staticmethod
    def _request(options: dict) -> dict:
        return {
            "schema_version": "0.6",
            "client_session_id": "session-a",
            "request_seq": 1,
            "request_id": "session-a:1",
            "operation": "emulate_action",
            "instance_id": "inst-001",
            "parent_branch_id": "root",
            "branch_id": "branch-1",
            "rng_id": 1,
            "decision_point_id": "decision-1",
            "action_id": "action-1",
            "simulation_options": options,
        }

    def test_limits_must_be_positive_integers(self) -> None:
        for field in ("max_depth", "max_steps", "max_time_ms", "max_hypotheses"):
            for value in (0, True, 1.5):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(RequestRejected, "positive integer"):
                        validate_request(self._request({field: value}))

    def test_valid_nullable_and_extension_options_are_preserved(self) -> None:
        request = self._request(
            {
                "max_depth": 1,
                "max_steps": None,
                "stop_condition": "next_decision",
                "future_extension": {"enabled": True},
            }
        )
        self.assertIs(validate_request(request), request)


if __name__ == "__main__":
    unittest.main()
