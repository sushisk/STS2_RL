from __future__ import annotations

import concurrent.futures
import threading
import time
import unittest

from API.identifiers import RequestLedger


class RequestLedgerRetryTest(unittest.TestCase):
    def test_identical_inflight_retry_waits_and_replays_without_reexecution(self) -> None:
        ledger = RequestLedger()
        payload = {
            "schema_version": "0.5",
            "request_id": "req-inflight",
            "operation": "start_instance",
            "instance_config": {"instance_type": "combat"},
        }
        owner_started = threading.Event()
        release_owner = threading.Event()
        execution_count = 0
        execution_lock = threading.Lock()

        def execute() -> dict:
            nonlocal execution_count
            cached = ledger.begin(payload)
            if cached is not None:
                return cached

            with execution_lock:
                execution_count += 1
            owner_started.set()
            release_owner.wait(timeout=2.0)
            response = {
                "schema_version": "0.5",
                "request_id": "req-inflight",
                "operation": "start_instance",
                "status": "completed",
                "instance_id": "inst-000001",
            }
            ledger.complete(payload, response)
            return response

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(execute)
            self.assertTrue(owner_started.wait(timeout=1.0))
            second = pool.submit(execute)
            time.sleep(0.05)
            self.assertEqual(execution_count, 1)
            release_owner.set()
            first_response = first.result(timeout=1.0)
            second_response = second.result(timeout=1.0)

        self.assertEqual(first_response, second_response)
        self.assertEqual(execution_count, 1)

    def test_abort_allows_one_waiter_to_take_over(self) -> None:
        ledger = RequestLedger()
        payload = {
            "schema_version": "0.5",
            "request_id": "req-abort",
            "operation": "start_instance",
            "instance_config": {"instance_type": "combat"},
        }
        self.assertIsNone(ledger.begin(payload))
        ledger.abort(payload)
        self.assertIsNone(ledger.begin(payload))


if __name__ == "__main__":
    unittest.main()
