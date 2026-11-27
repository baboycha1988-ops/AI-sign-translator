"""SYNTHETIC smoke test: proves the pipeline (features -> signer split -> baseline -> calibration -> open-set)
is wired correctly. It says NOTHING about accuracy on real sign language."""
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "api"))
from app.core.recognition import Thresholds, decide  # noqa: E402

from signml import features as F  # noqa: E402
from signml import metrics as M  # noqa: E402
from signml import splits as S  # noqa: E402
from signml.baseline import Baseline  # noqa: E402
from signml.calibrate import calibrate_thresholds  # noqa: E402


def make_sample(cls, signer_rng, noise_rng, T=30):
    """Gesture `cls` = a hand trajectory + finger spread pattern; the signer shifts, scales and speeds it."""
    dx, dy, k = signer_rng.uniform(-.15, .15), signer_rng.uniform(-.1, .1), signer_rng.uniform(.8, 1.3)
    speed = signer_rng.uniform(.85, 1.15)
    frames = []
    for t in range(T):
        u = speed * t / T
        cx = .5 + .15 * math.cos(2 * math.pi * (cls % 3 + 1) * u + cls)
        cy = .6 + .15 * math.sin(2 * math.pi * (cls // 3 + 1) * u)
        pose = [[.5, .3, 0.0] for _ in range(33)]
        pose[11], pose[12] = [.6, .5, 0.0], [.4, .5, 0.0]
        pose[16] = [cx, cy, 0.0]
        spread = .02 * (1 + cls % 4)
        hand = [[cx + spread * math.cos(j * (1 + cls % 5)), cy + spread * math.sin(j * (1 + cls % 5)), 0.0]
                for j in range(21)]
        n = lambda: noise_rng.gauss(0, .004)
        tf = lambda p: [k * p[0] + dx + n(), k * p[1] + dy + n(), p[2]]
        frames.append({"pose": [tf(p) for p in pose], "right_hand": [tf(p) for p in hand], "left_hand": None})
    return F.sequence_features(frames)


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        known, unknown_cls = list(range(6)), [6, 7, 8]
        signers = [f"p{i}" for i in range(14)]
        mapping = S.assign_signer_splits(signers, seed=11)
        cls.mapping = mapping
        noise = random.Random(5)
        data = {"train": [], "val": [], "test": []}
        for sg in signers:
            rng = random.Random(1000 + int(sg[1:]))
            for c in known + unknown_cls:
                for _ in range(8):
                    data[mapping[sg]].append((make_sample(c, rng, noise), c, sg))
        tr = [(s, c) for s, c, _ in data["train"] if c in known]
        cls.model = Baseline().fit([s for s, _ in tr], [str(c) for _, c in tr])

        def split(name):
            kn = [(*cls.model.predict(s), str(c)) for s, c, _ in data[name] if c in known]
            un = [cls.model.predict(s) for s, c, _ in data[name] if c in unknown_cls]
            return kn, un
        cls.val, cls.test = split("val"), split("test")
        cls.th = calibrate_thresholds(*cls.val, decide, Thresholds, max_false_accept=0.05)

    def test_signers_are_disjoint_across_splits(self):
        self.assertEqual(len(set(self.mapping.values())), 3)

    def test_known_signs_recognised_on_unseen_signers(self):
        kn, _ = self.test
        y = [t for _, _, t in kn]
        pred = [max(p, key=p.get) for p, _, _ in kn]
        acc = M.accuracy(y, pred)
        print(f"\n[synthetic] closed-set test accuracy: {acc:.3f} on {len(y)} samples")
        self.assertGreater(acc, 0.85)

    def test_calibration_found_thresholds(self):
        self.assertIsNotNone(self.th)

    def test_unknown_gestures_are_rejected_on_test_signers(self):
        kn, un = self.test
        far = sum(decide(p, d, self.th).status == "recognized" for p, d in un) / len(un)
        kept = sum((r := decide(p, d, self.th)).status == "recognized" and r.label == y for p, d, y in kn) / len(kn)
        print(f"[synthetic] thresholds={self.th}  false-accept on unknown={far:.3f}  known correctly accepted={kept:.3f}")
        self.assertLessEqual(far, 0.15)       # target was 0.05 on val; allow slack for a small test set
        # Honest trade-off: strict rejection of unknowns costs recall on known signs (the baseline's distance
        # score overlaps). We only require that it does not reject everything; real data decides the rest.
        self.assertGreater(kept, 0.25)

    def test_calibration_fails_closed_when_impossible(self):
        kn, un = self.val
        always_known = [(kn[0][0], kn[0][1])] * 20          # "unknown" samples identical to a confident known one
        th = calibrate_thresholds([kn[0]] * 20, always_known, decide, Thresholds, max_false_accept=0.0)
        self.assertIsNone(th)


class Metrics(unittest.TestCase):
    def test_matches_sklearn(self):
        from sklearn.metrics import precision_recall_fscore_support as prf
        yt = list("aabbbccdda"); yp = list("abbbcccdaa")
        labels = sorted(set(yt))
        mine = M.per_class_prf(yt, yp, labels)
        p, r, f, _ = prf(yt, yp, labels=labels, zero_division=0)
        for i, c in enumerate(labels):
            self.assertAlmostEqual(mine[c]["precision"], p[i]); self.assertAlmostEqual(mine[c]["recall"], r[i])
            self.assertAlmostEqual(mine[c]["f1"], f[i])


if __name__ == "__main__":
    unittest.main()
