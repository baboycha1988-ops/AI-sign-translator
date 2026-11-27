"""Is a proposed sign ready for training? One video is never enough (project rule)."""
from dataclasses import dataclass, field


@dataclass
class Readiness:
    ready: bool
    signers: int
    samples: int
    reasons: list = field(default_factory=list)


def training_readiness(samples, *, min_signers: int, min_samples: int) -> Readiness:
    """samples: iterable of (signer_id, qc_passed, label_verified). Only passed+verified count."""
    good = [s for s in samples if s[1] and s[2]]
    signers = len({s[0] for s in good})
    reasons = []
    if signers < min_signers:
        reasons.append(f"need {min_signers} different signers, have {signers}")
    if len(good) < min_samples:
        reasons.append(f"need {min_samples} verified samples, have {len(good)}")
    return Readiness(not reasons, signers, len(good), reasons)
