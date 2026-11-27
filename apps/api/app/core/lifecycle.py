"""Sign-proposal state machine and the model publication gate (mirrors the SQL trigger)."""
from dataclasses import dataclass, field
from typing import Mapping, Optional

TRANSITIONS = {
    "draft": {"submitted"},
    "submitted": {"in_review", "draft"},
    "in_review": {"approved", "rejected", "merged_duplicate"},
    "approved": {"collecting_samples"},
    "collecting_samples": {"ready_for_training"},
    "ready_for_training": {"in_training", "collecting_samples"},
    "in_training": {"evaluating", "failed"},
    "evaluating": {"published", "failed"},
    "failed": {"collecting_samples", "ready_for_training"},
    "rejected": {"draft"},
    "published": set(),
    "merged_duplicate": set(),
}


def can_transition(old: str, new: str) -> bool:
    return old == new or new in TRANSITIONS.get(old, set())


@dataclass
class GateResult:
    passed: bool
    reasons: list = field(default_factory=list)


def evaluate_gate(*, old_f1_before: Mapping, old_f1_after: Mapping, new_f1: Mapping,
                  min_new_sign_f1: Optional[float], max_regression: Optional[float]) -> GateResult:
    """Publish only if old signs did not get worse and every new sign reaches the threshold.

    All metrics must come from the same independent, signer-disjoint test set.
    Missing thresholds or metrics fail closed.
    """
    reasons = []
    if min_new_sign_f1 is None or max_regression is None:
        return GateResult(False, ["gate thresholds are not configured"])
    if not new_f1 and not old_f1_before:
        return GateResult(False, ["no metrics provided"])
    for sign, before in old_f1_before.items():
        after = old_f1_after.get(sign)
        if after is None:
            reasons.append(f"old sign {sign}: no metric after training")
        elif before - after > max_regression:
            reasons.append(f"old sign {sign}: F1 dropped {before:.3f} -> {after:.3f}")
    for sign, f1 in new_f1.items():
        if f1 < min_new_sign_f1:
            reasons.append(f"new sign {sign}: F1 {f1:.3f} < {min_new_sign_f1}")
    return GateResult(not reasons, reasons)
