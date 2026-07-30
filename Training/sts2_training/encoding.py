from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch


DICT_NAMES = {
    "action_type": "action_type_dict",
    "card": "card_dict",
    "potion": "potion_dict",
    "power": "power_dict",
    "relic": "relic_dict",
    "enemy": "enemy_dict",
    "character": "character_dict",
}

CARD_PILES = ("hand", "drawPile", "discardPile", "exhaustPile", "playPile", "deck")
MAX_ENEMIES = 6
MAX_POTIONS = 4


@dataclass(frozen=True)
class Vocab:
    token_to_id: dict[str, int]
    size: int

    def encode(self, token: Any) -> int:
        if token is None:
            return 0
        return self.token_to_id.get(str(token), 0)


class ExportEncoder:
    def __init__(self, id_dictionaries: dict[str, Any]) -> None:
        self.vocabs = {
            name: self._load_vocab(id_dictionaries[dict_name])
            for name, dict_name in DICT_NAMES.items()
        }
        self.state_dim = self._state_dim()
        self.action_numeric_dim = 7

    @staticmethod
    def _load_vocab(payload: dict[str, Any]) -> Vocab:
        mapping = {str(entry["token"]): int(entry["id"]) for entry in payload["entries"]}
        size = max(mapping.values(), default=0) + 1
        return Vocab(mapping, size)

    def vocab_size(self, name: str) -> int:
        return self.vocabs[name].size

    def encode_state(self, observation: dict[str, Any]) -> torch.Tensor:
        parts: list[torch.Tensor] = []
        parts.append(torch.tensor(self._state_scalars(observation), dtype=torch.float32))
        parts.append(self._one_hot("character", observation.get("characterId")))

        for pile in CARD_PILES:
            parts.append(self._bag("card", self._ids_from_items(observation.get(pile))))

        parts.append(self._bag("relic", self._ids_from_items(observation.get("relics"))))
        parts.append(self._bag("power", self._ids_from_items(observation.get("playerPowers"))))
        parts.append(self._bag("potion", self._ids_from_items(observation.get("potions"))))

        enemy_features: list[float] = []
        for enemy in (observation.get("enemies") or [])[:MAX_ENEMIES]:
            enemy_features.extend(
                [
                    self._num(enemy.get("hp"), 100.0),
                    self._num(enemy.get("maxHp"), 100.0),
                    self._num(enemy.get("block"), 100.0),
                    1.0 if enemy.get("isAlive") is True else 0.0,
                    self._intent_attack_damage(enemy) / 100.0,
                    self._intent_attack_repeats(enemy) / 10.0,
                ]
            )
            enemy_features.extend(self._one_hot_values("enemy", enemy.get("id")))
        while len(enemy_features) < MAX_ENEMIES * (6 + self.vocab_size("enemy")):
            enemy_features.append(0.0)
        parts.append(torch.tensor(enemy_features, dtype=torch.float32))

        return torch.cat(parts)

    def encode_action(self, action: dict[str, Any]) -> dict[str, torch.Tensor]:
        raw = action.get("raw_parameters") or {}
        return {
            "action_type": torch.tensor(self.vocabs["action_type"].encode(action.get("action_type")), dtype=torch.long),
            "card": torch.tensor(self.vocabs["card"].encode(action.get("card_id") or action.get("label")), dtype=torch.long),
            "potion": torch.tensor(self.vocabs["potion"].encode(action.get("potion_id") or action.get("label")), dtype=torch.long),
            "numeric": torch.tensor(
                [
                    1.0 if action.get("is_available") is True else 0.0,
                    self._target_type_code(action.get("target_type")),
                    self._optional_index(action.get("target_enemy_index"), MAX_ENEMIES),
                    self._optional_index(raw.get("potionSlot"), MAX_POTIONS),
                    self._num(raw.get("cost"), 10.0),
                    1.0 if action.get("card_id") is not None else 0.0,
                    1.0 if action.get("potion_id") is not None else 0.0,
                ],
                dtype=torch.float32,
            ),
        }

    def legal_mask(self, legal_actions: list[dict[str, Any]]) -> torch.Tensor:
        return torch.tensor([action.get("is_available") is True for action in legal_actions], dtype=torch.bool)

    def _state_dim(self) -> int:
        return (
            len(self._state_scalars({}))
            + self.vocab_size("character")
            + len(CARD_PILES) * self.vocab_size("card")
            + self.vocab_size("relic")
            + self.vocab_size("power")
            + self.vocab_size("potion")
            + MAX_ENEMIES * (6 + self.vocab_size("enemy"))
        )

    def _state_scalars(self, observation: dict[str, Any]) -> list[float]:
        enemies = observation.get("enemies") or []
        alive = [enemy for enemy in enemies if enemy.get("isAlive") is not False]
        return [
            self._num(observation.get("hp"), 100.0),
            self._num(observation.get("maxHp"), 100.0),
            self._num(observation.get("block"), 100.0),
            self._num(observation.get("energy"), 10.0),
            self._num(observation.get("maxEnergy"), 10.0),
            self._num(observation.get("stars"), 10.0),
            self._num(observation.get("gold"), 1000.0),
            self._num(observation.get("turnNumber"), 100.0),
            self._num(observation.get("combatRoundNumber"), 100.0),
            self._num(observation.get("stepIndex"), 1000.0),
            len(observation.get("hand") or []) / 20.0,
            len(observation.get("drawPile") or []) / 100.0,
            len(observation.get("discardPile") or []) / 100.0,
            len(observation.get("exhaustPile") or []) / 100.0,
            len(observation.get("deck") or []) / 100.0,
            len(alive) / float(MAX_ENEMIES),
            len(observation.get("potions") or []) / float(MAX_POTIONS),
            len(observation.get("relics") or []) / 50.0,
            len(observation.get("playerPowers") or []) / 50.0,
            1.0 if observation.get("pendingChoice") is not None else 0.0,
        ]

    def _bag(self, vocab_name: str, tokens: list[Any]) -> torch.Tensor:
        values = torch.zeros(self.vocab_size(vocab_name), dtype=torch.float32)
        for token in tokens:
            idx = self.vocabs[vocab_name].encode(token)
            if idx:
                values[idx] += 1.0
        if tokens:
            values /= max(1.0, float(len(tokens)))
        return values

    def _one_hot(self, vocab_name: str, token: Any) -> torch.Tensor:
        return torch.tensor(self._one_hot_values(vocab_name, token), dtype=torch.float32)

    def _one_hot_values(self, vocab_name: str, token: Any) -> list[float]:
        values = [0.0] * self.vocab_size(vocab_name)
        values[self.vocabs[vocab_name].encode(token)] = 1.0
        return values

    @staticmethod
    def _ids_from_items(items: Any) -> list[Any]:
        if not isinstance(items, list):
            return []
        return [item.get("id") for item in items if isinstance(item, dict)]

    @staticmethod
    def _num(value: Any, scale: float) -> float:
        if isinstance(value, bool) or value is None:
            return 0.0
        try:
            return float(value) / scale
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _target_type_code(value: Any) -> float:
        mapping = {
            None: 0.0,
            "None": 0.0,
            "Self": 0.2,
            "AnyPlayer": 0.4,
            "AnyEnemy": 0.6,
            "RandomEnemy": 0.7,
            "AllEnemies": 0.8,
        }
        return mapping.get(value, 1.0)

    @staticmethod
    def _optional_index(value: Any, scale: int) -> float:
        if value is None:
            return 0.0
        try:
            return (float(value) + 1.0) / float(scale + 1)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _intent_attack_damage(enemy: dict[str, Any]) -> float:
        intent = enemy.get("intent") if isinstance(enemy, dict) else None
        if not isinstance(intent, dict):
            return 0.0
        value = intent.get("attackDamage")
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

    @staticmethod
    def _intent_attack_repeats(enemy: dict[str, Any]) -> float:
        intent = enemy.get("intent") if isinstance(enemy, dict) else None
        if not isinstance(intent, dict):
            return 0.0
        value = intent.get("attackRepeats")
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

