"""python -m unittest discover -s tests -v   (run from packages/ml; standard library only)"""
import json
import math
import unittest
from pathlib import Path

from signml import features as F
from signml import splits as S

GOLDEN = Path(__file__).resolve().parents[2] / "spec" / "golden_features.json"


def synthetic_frame(dx=0.0, dy=0.0, k=1.0, left=True, right=True, shoulders=True):
    pose = [[0.5 + 0.001 * i, 0.4 + 0.002 * i, 0.01 * i] for i in range(33)]
    if shoulders:
        pose[11], pose[12] = [0.60, 0.50, 0.0], [0.40, 0.50, 0.0]
    else:
        pose[11] = pose[12] = None
    hand = lambda ox: [[ox + 0.01 * j, 0.7 - 0.005 * j, 0.002 * j] for j in range(21)]
    fr = {"pose": pose, "left_hand": hand(0.62) if left else None, "right_hand": hand(0.30) if right else None}
    def tf(p):
        return None if p is None else [k * p[0] + dx, k * p[1] + dy, k * p[2]]
    return {"pose": [tf(p) for p in fr["pose"]],
            "left_hand": None if fr["left_hand"] is None else [tf(p) for p in fr["left_hand"]],
            "right_hand": None if fr["right_hand"] is None else [tf(p) for p in fr["right_hand"]]}


class Features(unittest.TestCase):
    def test_dimensions(self):
        v = F.frame_features(synthetic_frame())
        self.assertEqual(len(v), F.BASE_DIM)
        self.assertEqual(F.BASE_DIM, 275)
        self.assertEqual(len(F.sequence_features([synthetic_frame()] * 3)[0]), F.FEATURE_DIM)

    def test_invariant_to_position_and_distance(self):
        a = F.frame_features(synthetic_frame())
        b = F.frame_features(synthetic_frame(dx=0.2, dy=-0.1, k=1.7))
        for x, y in zip(a, b):
            self.assertAlmostEqual(x, y, places=6)

    def test_missing_hand_is_zero_with_mask_off(self):
        v = F.frame_features(synthetic_frame(right=False))
        right_block = v[21 + 127:21 + 254]
        self.assertTrue(all(x == 0.0 for x in right_block))
        self.assertEqual(v[21 + 126], 1.0)      # left present
        self.assertEqual(v[21 + 254 - 1], 0.0)  # right absent

    def test_no_shoulders_means_no_features(self):
        self.assertIsNone(F.frame_features(synthetic_frame(shoulders=False)))

    def test_velocity_and_lost_frames(self):
        seq = F.sequence_features([synthetic_frame(), synthetic_frame(dx=0.0), synthetic_frame(shoulders=False)])
        self.assertTrue(all(abs(x) < 1e-12 for x in seq[1][F.BASE_DIM:]))   # static -> zero velocity
        self.assertTrue(all(x == 0.0 for x in seq[2]))                        # lost frame -> zeros

    def test_golden_file_matches(self):
        """Pins the spec for the JavaScript port. Regenerate only on a deliberate spec change."""
        got = F.sequence_features([synthetic_frame(), synthetic_frame(dx=0.01, dy=0.02)])
        if not GOLDEN.exists():
            GOLDEN.write_text(json.dumps({"frames_note": "synthetic_frame() in packages/ml/tests/test_ml.py",
                                          "sequence": got}), encoding="utf-8")
        want = json.loads(GOLDEN.read_text(encoding="utf-8"))["sequence"]
        self.assertEqual(len(got), len(want))
        for g, w in zip(got, want):
            for x, y in zip(g, w):
                self.assertTrue(math.isclose(x, y, abs_tol=1e-9))


class Splits(unittest.TestCase):
    def samples(self, n_signers=10, signs=("a", "b")):
        return [(f"{p}-{s}-{i}", p, s) for p in [f"p{j}" for j in range(n_signers)] for s in signs for i in range(2)]

    def test_no_signer_in_two_splits(self):
        smp = self.samples()
        m = S.assign_signer_splits([s[1] for s in smp], seed=7)
        manifest = S.build_manifest(smp, m)
        self.assertEqual(S.find_leakage(smp, manifest), [])
        self.assertEqual(set(m.values()), {"train", "val", "test"})

    def test_deterministic_and_seed_sensitive(self):
        ids = [f"p{j}" for j in range(12)]
        self.assertEqual(S.assign_signer_splits(ids, seed=1), S.assign_signer_splits(list(reversed(ids)), seed=1))
        self.assertNotEqual(S.assign_signer_splits(ids, seed=1), S.assign_signer_splits(ids, seed=2))

    def test_too_few_signers_refused(self):
        with self.assertRaises(ValueError):
            S.assign_signer_splits(["a", "b"], seed=1)

    def test_minimum_three_signers_gives_one_per_split(self):
        m = S.assign_signer_splits(["a", "b", "c"], seed=3)
        self.assertEqual(sorted(m.values()), ["test", "train", "val"])

    def test_leakage_detector_catches_manual_mistake(self):
        smp = [("1", "p0", "a"), ("2", "p0", "a")]
        self.assertEqual(S.find_leakage(smp, {"1": "train", "2": "test"}), ["p0"])

    def test_uncovered_sign_is_reported(self):
        smp = [("1", "p0", "rare"), ("2", "p1", "a"), ("3", "p2", "a"), ("4", "p3", "a")]
        m = {"p0": "train", "p1": "train", "p2": "val", "p3": "test"}
        rep = S.coverage_report(smp, m)
        self.assertEqual(rep["uncovered_signs"], ["rare"])


if __name__ == "__main__":
    unittest.main()
