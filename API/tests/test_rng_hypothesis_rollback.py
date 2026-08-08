from __future__ import annotations

from API.identifiers import RngHypothesisTable


def test_rng_snapshot_is_constant_size_and_restores_only_new_allocations() -> None:
    table = RngHypothesisTable()

    for rng_id in range(10_000):
        assert table.hypothesis_index_for("root", "d-root-000001", rng_id) == rng_id

    snapshot = table.snapshot()
    assert snapshot == 10_000
    assert isinstance(snapshot, int)

    assert table.hypothesis_index_for("root", "d-root-000001", 10_000) == 10_000
    assert table.hypothesis_index_for("parent", "d-parent-000001", 1) == 0
    assert table.hypothesis_index_for("root", "d-root-000001", 10_001) == 10_001

    table.restore(snapshot)

    assert len(table._index_by_key) == 10_000  # noqa: SLF001
    assert table._next_index_by_parent_decision == {  # noqa: SLF001
        ("root", "d-root-000001"): 10_000,
    }
    assert table.hypothesis_index_for("root", "d-root-000001", 10_000) == 10_000
    assert table.hypothesis_index_for("parent", "d-parent-000001", 1) == 0


def test_rng_restore_rejects_stale_or_invalid_markers() -> None:
    table = RngHypothesisTable()
    table.hypothesis_index_for("root", "d-root-000001", 1)

    for invalid in (-1, 2):
        try:
            table.restore(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"restore({invalid}) must reject an invalid marker")

    try:
        table.restore(True)
    except TypeError:
        pass
    else:
        raise AssertionError("boolean markers must not be accepted as integer snapshots")
