"""Choose open-set thresholds on VALIDATION data only (never on test data).

known:   [(probs, distance, true_label)]   signs the model was trained on
unknown: [(probs, distance)]               gestures outside the vocabulary / random movements
Maximises correctly accepted known gestures subject to false-accept rate on unknown <= max_false_accept.
Returns None when the constraint cannot be met: then the model must NOT be published (fail closed)."""
import numpy as np


def calibrate_thresholds(known, unknown, decide, thresholds_cls, *, max_false_accept=0.05):
    if not known or not unknown:
        return None
    dists = np.array([d for _, d, _ in known if d is not None])
    qs = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0)
    dist_grid = sorted({float(np.quantile(dists, q)) for q in qs}) if len(dists) else []
    conf_grid = [round(x, 2) for x in np.arange(0.3, 0.99, 0.05)]
    margin_grid = [round(x, 2) for x in np.arange(0.0, 0.45, 0.05)]
    best, best_key = None, None
    for c in conf_grid:
        for d in dist_grid:
            for m in margin_grid:
                th = thresholds_cls(float(c), float(d), float(m))
                far = sum(decide(p, dist, th).status == "recognized" for p, dist in unknown) / len(unknown)
                if far > max_false_accept:
                    continue
                ok = sum((r := decide(p, dist, th)).status == "recognized" and r.label == y
                         for p, dist, y in known) / len(known)
                key = (ok, -far, c, m, -d)          # prefer more correct, then stricter settings
                if best_key is None or key > best_key:
                    best, best_key = th, key
    return best
