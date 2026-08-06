from __future__ import annotations

import torch
from torch import nn


class CandidatePolicyNet(nn.Module):
    def __init__(
        self,
        state_dim: int,
        action_numeric_dim: int,
        action_type_vocab: int,
        card_vocab: int,
        potion_vocab: int,
        hidden_dim: int = 128,
        embedding_dim: int = 32,
    ) -> None:
        super().__init__()
        self.action_type_embedding = nn.Embedding(action_type_vocab, 8)
        self.card_embedding = nn.Embedding(card_vocab, embedding_dim)
        self.potion_embedding = nn.Embedding(potion_vocab, 16)
        action_dim = 8 + embedding_dim + 16 + action_numeric_dim
        self.state_net = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        self.action_net = nn.Sequential(
            nn.Linear(action_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        self.scorer = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )

    def forward(
        self,
        state: torch.Tensor,
        action_type: torch.Tensor,
        card: torch.Tensor,
        potion: torch.Tensor,
        action_numeric: torch.Tensor,
    ) -> torch.Tensor:
        state_repr = self.state_net(state)
        action_repr = torch.cat(
            [
                self.action_type_embedding(action_type),
                self.card_embedding(card),
                self.potion_embedding(potion),
                action_numeric,
            ],
            dim=-1,
        )
        action_repr = self.action_net(action_repr)
        expanded_state = state_repr.unsqueeze(1).expand(-1, action_repr.shape[1], -1)
        return self.scorer(torch.cat([expanded_state, action_repr], dim=-1)).squeeze(-1)


def masked_logits(logits: torch.Tensor, legal_mask: torch.Tensor) -> torch.Tensor:
    return logits.masked_fill(~legal_mask, -1.0e9)


class ValueNet(nn.Module):
    """State-only value model: win probability, expected final HP, expected remaining decisions.

    Three independent heads on a shared trunk, so RL-side callers can weight them however they
    like at inference/search time instead of Training baking in a single scalar value.
    """

    def __init__(self, state_dim: int, hidden_dim: int = 128) -> None:
        super().__init__()
        self.trunk = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        self.win_probability_head = nn.Linear(hidden_dim, 1)
        self.final_hp_head = nn.Linear(hidden_dim, 1)
        self.remaining_decisions_head = nn.Linear(hidden_dim, 1)

    def forward(self, state: torch.Tensor) -> dict[str, torch.Tensor]:
        hidden = self.trunk(state)
        return {
            "win_logit": self.win_probability_head(hidden).squeeze(-1),
            "final_hp": self.final_hp_head(hidden).squeeze(-1),
            "remaining_decisions": self.remaining_decisions_head(hidden).squeeze(-1),
        }


class ChoicePolicyNet(nn.Module):
    """Ranks candidate cards for one choice_card decision.

    state_net and card_embedding are shaped to match CandidatePolicyNet's State/Card Encoder so a
    trained normal-Policy checkpoint's weights can be loaded in (see load_shared_encoder_weights)
    and optionally frozen. action_net/scorer are Choice-specific new heads: normal Policy's
    weights/interface are never modified by this class.
    """

    def __init__(
        self,
        state_dim: int,
        card_vocab: int,
        choice_meaning_vocab: int,
        card_embedding_dim: int = 32,
        choice_meaning_embedding_dim: int = 8,
        hidden_dim: int = 64,
        use_choice_meaning: bool = True,
    ) -> None:
        super().__init__()
        self.use_choice_meaning = use_choice_meaning
        self.card_embedding = nn.Embedding(card_vocab, card_embedding_dim)
        self.choice_meaning_embedding = nn.Embedding(
            choice_meaning_vocab, choice_meaning_embedding_dim
        )
        self.state_net = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        action_dim = (
            card_embedding_dim
            + (choice_meaning_embedding_dim if use_choice_meaning else 0)
            + 1
        )
        self.action_net = nn.Sequential(
            nn.Linear(action_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        self.scorer = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )

    def set_shared_encoder_trainable(self, trainable: bool) -> None:
        for param in self.state_net.parameters():
            param.requires_grad = trainable
        for param in self.card_embedding.parameters():
            param.requires_grad = trainable

    def forward(
        self,
        state: torch.Tensor,
        card: torch.Tensor,
        choice_meaning: torch.Tensor,
        remaining_select_count: torch.Tensor,
    ) -> torch.Tensor:
        state_repr = self.state_net(state)
        num_candidates = card.shape[1]
        parts = [self.card_embedding(card)]
        if self.use_choice_meaning:
            meaning_repr = (
                self.choice_meaning_embedding(choice_meaning)
                .unsqueeze(1)
                .expand(-1, num_candidates, -1)
            )
            parts.append(meaning_repr)
        remaining_repr = remaining_select_count.view(-1, 1, 1).expand(
            -1, num_candidates, 1
        )
        parts.append(remaining_repr)
        action_repr = self.action_net(torch.cat(parts, dim=-1))
        expanded_state = state_repr.unsqueeze(1).expand(-1, num_candidates, -1)
        return self.scorer(torch.cat([expanded_state, action_repr], dim=-1)).squeeze(-1)


def load_shared_encoder_weights(
    choice_model: ChoicePolicyNet, policy_model_state: dict[str, torch.Tensor]
) -> list[str]:
    """Copy state_net + card_embedding weights from a trained CandidatePolicyNet checkpoint.

    Returns the list of parameter keys actually copied (for provenance/logging).
    """
    own_state = choice_model.state_dict()
    copied: list[str] = []
    for key, tensor in policy_model_state.items():
        if not key.startswith(("state_net.", "card_embedding.")):
            continue
        if key in own_state and own_state[key].shape == tensor.shape:
            own_state[key] = tensor.clone()
            copied.append(key)
    choice_model.load_state_dict(own_state)
    return copied
