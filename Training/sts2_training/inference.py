from __future__ import annotations

from pathlib import Path
from typing import Any

import torch

from .encoding import ExportEncoder
from .model import CandidatePolicyNet, ValueNet, masked_logits
from .value_targets import REMAINING_DECISIONS_SCALE


def _load_checkpoint(checkpoint_path: Path) -> dict[str, Any]:
    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    if "dictionaries" not in checkpoint:
        raise ValueError(
            f"{checkpoint_path} has no embedded 'dictionaries'; re-save it with the current "
            "train_imitation.py / train_value.py so inference does not depend on the export directory."
        )
    return checkpoint


def _provenance(config: dict[str, Any], checkpoint_path: Path) -> dict[str, Any]:
    return {
        "checkpoint_path": str(checkpoint_path),
        "export_root": config.get("export_root"),
        "dataset_version": config.get("dataset_version"),
        "contract_version": config.get("contract_version"),
        "dictionary_version": config.get("dictionary_version"),
        "export_script_version": config.get("export_script_version"),
        "emulator_commit": config.get("emulator_commit"),
        "emulator_dll_sha256": config.get("emulator_dll_sha256"),
        "heuristic_version": config.get("heuristic_version"),
    }


class PolicyDecision:
    """Loads a CandidatePolicyNet checkpoint and ranks legal_actions for one decision.

    Self-contained: only needs the checkpoint file (dictionaries are embedded in it at save
    time), no Emulator/pythonnet and no access to the export directory.
    """

    def __init__(self, checkpoint_path: str | Path) -> None:
        checkpoint_path = Path(checkpoint_path)
        checkpoint = _load_checkpoint(checkpoint_path)
        self.config = checkpoint["config"]
        self.encoder = ExportEncoder(checkpoint["dictionaries"])
        self.model = CandidatePolicyNet(
            state_dim=self.encoder.state_dim,
            action_numeric_dim=self.encoder.action_numeric_dim,
            action_type_vocab=self.encoder.vocab_size("action_type"),
            card_vocab=self.encoder.vocab_size("card"),
            potion_vocab=self.encoder.vocab_size("potion"),
            hidden_dim=self.config["hidden_dim"],
        )
        self.model.load_state_dict(checkpoint["model_state"])
        self.model.eval()
        self.provenance = _provenance(self.config, checkpoint_path)

    def __call__(self, observation: dict[str, Any], legal_actions: list[dict[str, Any]]) -> dict[str, Any]:
        if not legal_actions:
            raise ValueError("legal_actions must be non-empty")

        # The model was never trained on choice_card decisions (RL does not yet supply choice
        # purpose/source-zone/origin context, and Training must not guess it). Detected
        # structurally from action_type, which the Data Contract already guarantees is present.
        recommend_heuristic_fallback = any(a.get("action_type") == "choice_card" for a in legal_actions)

        action_features = [self.encoder.encode_action(a) for a in legal_actions]
        state = self.encoder.encode_state(observation).unsqueeze(0)
        action_type = torch.stack([f["action_type"] for f in action_features]).unsqueeze(0)
        card = torch.stack([f["card"] for f in action_features]).unsqueeze(0)
        potion = torch.stack([f["potion"] for f in action_features]).unsqueeze(0)
        numeric = torch.stack([f["numeric"] for f in action_features]).unsqueeze(0)
        legal_mask = self.encoder.legal_mask(legal_actions).unsqueeze(0)

        with torch.no_grad():
            logits = self.model(state, action_type, card, potion, numeric)
            masked = masked_logits(logits, legal_mask)
            probabilities = torch.softmax(masked, dim=-1)[0]

        ranked_action_indices = torch.argsort(masked[0], descending=True).tolist()
        selected_action_index = ranked_action_indices[0]
        return {
            "selected_action_index": selected_action_index,
            "confidence": float(probabilities[selected_action_index].item()),
            "ranked_action_indices": ranked_action_indices,
            "recommend_heuristic_fallback": recommend_heuristic_fallback,
            "provenance": self.provenance,
        }


class ValueDetermination:
    """Loads a ValueNet checkpoint and scores a single observation (state only, no actions)."""

    def __init__(self, checkpoint_path: str | Path) -> None:
        checkpoint_path = Path(checkpoint_path)
        checkpoint = _load_checkpoint(checkpoint_path)
        self.config = checkpoint["config"]
        self.encoder = ExportEncoder(checkpoint["dictionaries"])
        self.model = ValueNet(state_dim=self.encoder.state_dim, hidden_dim=self.config["hidden_dim"])
        self.model.load_state_dict(checkpoint["model_state"])
        self.model.eval()
        self.remaining_decisions_scale = float(self.config.get("remaining_decisions_scale", REMAINING_DECISIONS_SCALE))
        self.provenance = _provenance(self.config, checkpoint_path)

    def __call__(self, observation: dict[str, Any]) -> dict[str, Any]:
        state = self.encoder.encode_state(observation).unsqueeze(0)
        with torch.no_grad():
            output = self.model(state)
        win_probability = float(torch.sigmoid(output["win_logit"])[0].item())
        final_hp_fraction = float(output["final_hp"][0].item())
        max_hp = observation.get("maxHp") or 1.0
        return {
            "win_probability": win_probability,
            "expected_final_hp_fraction": final_hp_fraction,
            "expected_final_hp": final_hp_fraction * float(max_hp),
            "expected_remaining_decisions": float(output["remaining_decisions"][0].item()) * self.remaining_decisions_scale,
            "provenance": self.provenance,
        }
