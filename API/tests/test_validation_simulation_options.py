from __future__ import annotations

import unittest

from API.validation import RequestRejected, validate_request


class SimulationOptionsValidationTest(unittest.TestCase):
    @staticmethod
    def _request(simulation_options: dict) -> dict:
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
            "simulation_options": simulation_options,
        }

    def test_positive_integer_limits_are_required_when_present(self) -> None:
        for field in ("max_depth", "max_steps", "max_time_ms", "max_hypotheses"):
            for invalid in (0, -1, True):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaisesRegex(RequestRejected, "positive integer|type int"):
                        validate_request(self._request({field: invalid}))

    def test_non_integer_limit_is_rejected(self) -> None:
        with self.assertRaisesRegex(RequestRejected, "type int"):
            validate_request(self._request({"max_depth": 1.5}))

    def test_valid_limits_and_unknown_extension_keys_remain_accepted(self) -> None:
        request = self._request(
            {
                "max_depth": 1,
                "max_steps": 2,
                "max_time_ms": 3,
                "max_hypotheses": 4,
                "stop_condition": "next_decision",
                "future_extension": {"enabled": True},
            }
        )
        self.assertIs(validate_request(request), request)

    def test_none_values_remain_accepted_for_optional_known_fields(self) -> None:
        request = self._request(
            {
                "max_depth": None,
                "stop_condition": None,
            }
        )
        self.assertIs(validate_request(request), request)


if __name__ == "__main__":
    unittest.main()
