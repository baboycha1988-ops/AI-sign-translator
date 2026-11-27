"""Signer-independent dataset splits. The unit of splitting is the PERSON, never the video:
if one signer appears in train and test, accuracy is inflated because the model learns the person."""
import random
from collections import defaultdict


def assign_signer_splits(signer_ids, *, seed, fractions=(0.7, 0.15, 0.15)):
    ids = sorted(set(signer_ids))
    n = len(ids)
    if n < 3:
        raise ValueError(f"need at least 3 distinct signers for train/val/test, got {n}")
    train_f, val_f, test_f = fractions
    if abs(train_f + val_f + test_f - 1.0) > 1e-9:
        raise ValueError("fractions must sum to 1")
    random.Random(seed).shuffle(ids)
    n_test = max(1, round(n * test_f))
    n_val = max(1, round(n * val_f))
    while n - n_test - n_val < 1:
        if n_test >= n_val and n_test > 1:
            n_test -= 1
        elif n_val > 1:
            n_val -= 1
        else:
            break
    mapping = {}
    for i, s in enumerate(ids):
        mapping[s] = "test" if i < n_test else "val" if i < n_test + n_val else "train"
    return mapping


def build_manifest(samples, mapping):
    """samples: iterable of (sample_id, signer_id, sign_id) -> {sample_id: split}"""
    return {sid: mapping[signer] for sid, signer, _ in samples}


def coverage_report(samples, mapping):
    """Per sign: sample and distinct-signer count in each split; signs that cannot be evaluated."""
    cnt = defaultdict(lambda: defaultdict(int))
    for _, signer, sign in samples:
        cnt[sign][mapping[signer]] += 1
    uncovered = sorted(sign for sign, c in cnt.items() if any(c.get(s, 0) == 0 for s in ("train", "val", "test")))
    return {"per_sign": {k: dict(v) for k, v in cnt.items()}, "uncovered_signs": uncovered}


def find_leakage(samples, manifest):
    """Signers that occur in more than one split (must be empty)."""
    seen = defaultdict(set)
    for sid, signer, _ in samples:
        seen[signer].add(manifest[sid])
    return sorted(s for s, splits in seen.items() if len(splits) > 1)
