import unittest
from app.core.readiness import training_readiness


class Readiness(unittest.TestCase):
    def test_single_video_is_never_enough(self):
        r = training_readiness([("p1", True, True)], min_signers=3, min_samples=15)
        self.assertFalse(r.ready)
        self.assertEqual(len(r.reasons), 2)

    def test_ready(self):
        s = [(f"p{i % 3}", True, True) for i in range(15)]
        self.assertTrue(training_readiness(s, min_signers=3, min_samples=15).ready)

    def test_unverified_or_failed_samples_do_not_count(self):
        s = [(f"p{i}", False, True) for i in range(20)] + [(f"q{i}", True, False) for i in range(20)]
        r = training_readiness(s, min_signers=3, min_samples=15)
        self.assertFalse(r.ready)
        self.assertEqual((r.signers, r.samples), (0, 0))

    def test_many_samples_from_one_person_not_enough(self):
        s = [("p1", True, True)] * 40
        self.assertFalse(training_readiness(s, min_signers=3, min_samples=15).ready)


if __name__ == "__main__":
    unittest.main()
