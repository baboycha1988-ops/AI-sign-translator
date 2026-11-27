"""Open-set decision: an unknown gesture must never turn into a random word.

A gesture is accepted only if ALL three conditions hold:
  1. calibrated confidence >= min_confidence
  2. distance to the nearest prototype <= max_distance
  3. margin over the second candidate >= min_margin
Missing information fails closed. Thresholds are calibrated per model on the
validation set and stored in ai_models.gate_report, not hard-coded.
"""
import math
from dataclasses import dataclass, field
from typing import Mapping, Optional


@dataclass(frozen=True)
class Thresholds:
    min_confidence: float
    max_distance: float
    min_margin: float


@dataclass
class Decision:
    status: str                      # recognized | ambiguous | unknown
    label: Optional[str]
    confidence: float
    alternatives: list = field(default_factory=list)
    reasons: list = field(default_factory=list)


def decide(probs: Mapping, distance: Optional[float], th: Thresholds) -> Decision:
    clean = {k: float(v) for k, v in probs.items() if isinstance(v, (int, float)) and math.isfinite(v) and 0.0 <= v <= 1.0}
    if not clean or len(clean) != len(probs):
        return Decision("unknown", None, 0.0, reasons=["no_valid_model_output"])
    ranked = sorted(clean.items(), key=lambda kv: kv[1], reverse=True)
    (top, conf) = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = conf - second

    reasons = []
    if conf < th.min_confidence:
        reasons.append("low_confidence")
    if distance is None or not math.isfinite(distance):
        reasons.append("no_distance")
    elif distance > th.max_distance:
        reasons.append("far_from_prototype")
    if margin < th.min_margin:
        reasons.append("low_margin")

    if not reasons:
        return Decision("recognized", top, conf, ranked[:3])
    if reasons == ["low_margin"] and len(ranked) > 1:
        return Decision("ambiguous", None, conf, ranked[:2], reasons)
    return Decision("unknown", None, conf, [], reasons)
