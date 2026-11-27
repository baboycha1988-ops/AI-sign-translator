import math
import unittest

from signml import metrics as M
from signml.qc import qc_sample
from test_ml import synthetic_frame


def moving(n, fps=25, **kw):
    out = []
    for t in range(n):
        f = synthetic_frame(**kw)
        f["pose"][16] = [0.3 + 0.2 * math.sin(t / 4), 0.5 + 0.1 * math.cos(t / 4), 0.0]
        out.append(f)
    return out


class QC(unittest.TestCase):
    def test_good_sample_passes_and_reports_segment(self):
        r = qc_sample(moving(40), fps=25, brightness=[120] * 40, sharpness=[80] * 40)
        self.assertTrue(r.passed, r.reasons)
        self.assertEqual(r.segment, (0, 39))

    def test_each_failure_is_reported(self):
        cases = {
            "hands": qc_sample(moving(40, left=False, right=False), fps=25),
            "short": qc_sample(moving(5), fps=25),
            "long": qc_sample(moving(400), fps=25),
            "fps": qc_sample(moving(40), fps=8),
            "shoulders": qc_sample(moving(40, shoulders=False), fps=25),
            "dark": qc_sample(moving(40), fps=25, brightness=[10] * 40),
            "blur": qc_sample(moving(40), fps=25, sharpness=[1] * 40),
            "static": qc_sample([synthetic_frame()] * 40, fps=25),
        }
        for name, r in cases.items():
            self.assertFalse(r.passed, name)
            self.assertTrue(r.reasons, name)

    def test_empty(self):
        self.assertFalse(qc_sample([], fps=25).passed)


class Kappa(unittest.TestCase):
    def test_matches_sklearn(self):
        from sklearn.metrics import cohen_kappa_score
        a = list("aabbbccdda"); b = list("abbbcccdaa")
        self.assertAlmostEqual(M.cohen_kappa(a, b), cohen_kappa_score(a, b))

    def test_perfect_and_invalid(self):
        self.assertEqual(M.cohen_kappa(list("abab"), list("abab")), 1.0)
        with self.assertRaises(ValueError):
            M.cohen_kappa([], [])


if __name__ == "__main__":
    unittest.main()
