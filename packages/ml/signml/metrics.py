"""Precision / recall / F1 and confusion pairs (no sklearn dependency at runtime)."""
from collections import Counter


def per_class_prf(y_true, y_pred, labels):
    out = {}
    for c in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == c and p != c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[c] = {"precision": prec, "recall": rec, "f1": f1, "support": tp + fn}
    return out


def macro(per_class, key):
    return sum(v[key] for v in per_class.values()) / len(per_class) if per_class else 0.0


def accuracy(y_true, y_pred):
    return sum(t == p for t, p in zip(y_true, y_pred)) / len(y_true) if y_true else 0.0


def top_confusions(y_true, y_pred, k=5):
    pairs = Counter((t, p) for t, p in zip(y_true, y_pred) if t != p)
    return pairs.most_common(k)


def cohen_kappa(a, b):
    """Agreement between two annotators (1 = perfect, 0 = chance level)."""
    if len(a) != len(b) or not a:
        raise ValueError("label lists must be equal-length and non-empty")
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb.get(k, 0) for k in ca) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)
