"""Incremental past Event/Encounter history builder (contract §3 "過去のEvent／
Encounter履歴").

Built ONLY from actual observed `RoomContext`/committed-action data as root (or a
Branch) progresses - never sliced out of a pre-generated Event/Encounter Queue (the
contract is explicit: "履歴は実際のTransitionから逐次構築し、事前生成列から作成しない").
One `HistoryBuilder` is owned per logical progression line (root, or a Branch that wants
its own extended history) and accumulates monotonically; `fork()` gives a Branch a copy
of its parent's history to extend independently without mutating the parent's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class HistoryEntry:
    kind: str  # "room_visited" | "event_option_selected" | "encounter_completed" | "elite_or_boss"
    detail: dict

    def to_public_dict(self) -> dict:
        return {"kind": self.kind, **self.detail}


@dataclass
class HistoryBuilder:
    _entries: list = field(default_factory=list)
    _seen_coordinates: set = field(default_factory=set)

    def fork(self) -> "HistoryBuilder":
        return HistoryBuilder(list(self._entries), set(self._seen_coordinates))

    def observe_room_context(self, room_context: dict) -> None:
        if not room_context or not room_context.get("in_room"):
            return
        column, row = room_context.get("column"), room_context.get("row")
        if column is None or row is None:
            return
        coordinate = (column, row)
        if coordinate in self._seen_coordinates:
            return
        self._seen_coordinates.add(coordinate)
        room_type = room_context.get("room_type")
        self._entries.append(
            HistoryEntry("room_visited", {"column": column, "row": row, "room_type": room_type})
        )
        if room_type in ("Elite", "Boss"):
            self._entries.append(HistoryEntry("elite_or_boss", {"column": column, "row": row, "room_type": room_type}))

    def observe_event_option_selected(self, event_id: "str | None", option_id: "str | None") -> None:
        if event_id is None and option_id is None:
            return
        self._entries.append(HistoryEntry("event_option_selected", {"event_id": event_id, "option_id": option_id}))

    def observe_encounter_completed(self, monster_ids: list) -> None:
        if not monster_ids:
            return
        self._entries.append(HistoryEntry("encounter_completed", {"monster_ids": list(monster_ids)}))

    def to_public_list(self) -> list[dict]:
        return [entry.to_public_dict() for entry in self._entries]
