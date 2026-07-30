from __future__ import annotations

from pathlib import Path
from typing import Any

import torch

from .choice_data import choice_card_candidates
from .encoding import ExportEncoder, Vocab
from .model import ChoicePolicyNet, masked_logits


def _load_checkpoint(checkpoint_path: Path) -> dict[str, Any]:
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    for required in ("dictionaries", "choice_meaning_dict", "merge_map"):
        if required not in checkpoint:
            raise ValueError(f"{checkpoint_path} is missing '{required}'; re-save with train_choice_policy_8token.py")
    return checkpoint


class ChoiceDecision:
    """Offline Choice Policy inference interface. Does not connect to any Combat adapter -
    callers are responsible for wiring this into an online loop if/when that is approved.

    Input: battle_state, the decision's legal_actions (only choice_card entries are ranked -
    choice_confirm/choice_skip are ignored, same scope as training), operationMode, one of
    normalizedChoiceOperation/exceptionEntityKey, remainingSelectCount.

    Output: ranking of legal choice_card candidates, top-1 action_id, top-1 confidence, top-2
    confidence, confidence margin, and a fallback_reason (non-null whenever the caller should
    prefer the Heuristic instead of trusting this ranking).
    """

    def __init__(self, checkpoint_path: str | Path) -> None:
        checkpoint_path = Path(checkpoint_path)
        checkpoint = _load_checkpoint(checkpoint_path)
        self.config = checkpoint["config"]
        self.provenance = checkpoint["provenance"]
        self.merge_map: dict[str, str] = checkpoint["merge_map"]
        self.encoder = ExportEncoder(checkpoint["dictionaries"])
        choice_meaning_dict = checkpoint["choice_meaning_dict"]
        self.choice_meaning_vocab = Vocab(
            token_to_id={e["token"]: e["id"] for e in choice_meaning_dict["entries"]},
            size=len(choice_meaning_dict["entries"]),
        )
        self.model = ChoicePolicyNet(
            state_dim=self.config["state_dim"],
            card_vocab=self.config["card_vocab"],
            choice_meaning_vocab=self.config["choice_meaning_vocab"],
            card_embedding_dim=self.config["card_embedding_dim"],
            choice_meaning_embedding_dim=self.config["choice_meaning_embedding_dim"],
            hidden_dim=self.config["hidden_dim"],
            use_choice_meaning=True,
        )
        self.model.load_state_dict(checkpoint["model_state"])
        self.model.eval()

    def __call__(
        self,
        battle_state: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        operation_mode: str | None,
        normalized_choice_operation: str | None = None,
        exception_entity_key: str | None = None,
        remaining_select_count: float = 0.0,
    ) -> dict[str, Any]:
        candidates = [a for a in legal_actions if a.get("action_type") == "choice_card"]
        if not candidates:
            return self._fallback_result("no_choice_card_candidates", candidates=[])
        if operation_mode == "unknown" or operation_mode is None:
            return self._fallback_result("operation_mode_unknown", candidates=candidates)

        raw_token = normalized_choice_operation or exception_entity_key
        merged_token = self.merge_map.get(raw_token, raw_token) if raw_token else None
        meaning_id = self.choice_meaning_vocab.encode(merged_token)
        fallback_reason = None
        if meaning_id == 0:
            # unrecognized meaning token (not seen during training) - ranking is still produced
            # (Card Encoder alone still carries most of the signal, per the analysis report),
            # but callers should treat it as lower-trust and may prefer the Heuristic.
            fallback_reason = "unrecognized_choice_meaning_token"

        state = self.encoder.encode_state(battle_state).unsqueeze(0)
        card_ids = torch.tensor(
            [[self.encoder.vocabs["card"].encode(c["parameters"].get("cardId") or c.get("label")) for c in candidates]],
            dtype=torch.long,
        )
        meaning_tensor = torch.tensor([meaning_id], dtype=torch.long)
        remaining_tensor = torch.tensor([float(remaining_select_count or 0.0)], dtype=torch.float32)
        candidate_mask = torch.ones(1, len(candidates), dtype=torch.bool)

        with torch.no_grad():
            logits = self.model(state, card_ids, meaning_tensor, remaining_tensor)
        masked = masked_logits(logits, candidate_mask)[0]
        probs = torch.softmax(masked, dim=0)
        ranking = torch.argsort(masked, descending=True).tolist()

        top1_idx = ranking[0]
        top2_idx = ranking[1] if len(ranking) > 1 else None
        top1_confidence = float(probs[top1_idx].item())
        top2_confidence = float(probs[top2_idx].item()) if top2_idx is not None else 0.0

        return {
            "ranking": [{"index": i, "action_id": candidates[i].get("action_id"), "label": candidates[i].get("label")} for i in ranking],
            "top1_action_id": candidates[top1_idx].get("action_id"),
            "top1_confidence": top1_confidence,
            "top2_confidence": top2_confidence,
            "confidence_margin": top1_confidence - top2_confidence,
            "fallback_reason": fallback_reason,
            "provenance": self.provenance,
        }

    def _fallback_result(self, reason: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "ranking": [{"index": i, "action_id": c.get("action_id"), "label": c.get("label")} for i, c in enumerate(candidates)],
            "top1_action_id": candidates[0].get("action_id") if candidates else None,
            "top1_confidence": None,
            "top2_confidence": None,
            "confidence_margin": None,
            "fallback_reason": reason,
            "provenance": self.provenance,
        }
