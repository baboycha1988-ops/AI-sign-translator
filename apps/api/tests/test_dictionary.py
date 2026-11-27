import unittest
from app.dictionary_service import Actor, DictRepo, DictionaryService, DomainError, ProposalService

USER = Actor("u1", ["user"]); ADMIN = Actor("a1", ["user", "admin"]); NOBODY = Actor("x", [])
TR = [("tg", "салом"), ("ru", "привет"), ("en", "hello")]


def env():
    r = DictRepo()
    return r, DictionaryService(r), ProposalService(r)


def add_samples(r, pid, signers, per=5):
    for s in signers:
        for _ in range(per):
            r.samples.append({"proposal_id": pid, "sign_id": None, "signer_id": s, "qc_passed": True, "verified": True})


class Proposals(unittest.TestCase):
    def test_full_happy_path_to_published(self):
        r, d, p = env()
        pid = p.submit(USER, gloss="HELLO", sign_language="tajik-sl", translations=TR)["proposal"]["id"]
        sign = p.review(ADMIN, pid, "approve")["sign_id"]
        self.assertEqual(r.signs[sign]["status"], "approved")
        self.assertFalse(d.search("hello", "en")[0]["recognizable"])        # approved != recognizable
        p.start_collecting(ADMIN, pid)
        add_samples(r, pid, ["s1", "s2", "s3"])
        p.mark_ready(ADMIN, pid); p.start_training(ADMIN, pid); p.finish_training(ADMIN, pid)
        res = p.conclude(ADMIN, pid, old_f1_before={}, old_f1_after={}, new_f1={sign: 0.9},
                         min_new_sign_f1=0.8, max_regression=0.02)
        self.assertTrue(res.passed)
        self.assertEqual(r.signs[sign]["status"], "active")
        self.assertTrue(d.search("hello", "en")[0]["recognizable"])

    def test_failed_gate_never_activates_sign(self):
        r, d, p = env()
        pid = p.submit(USER, gloss="HELLO", sign_language="tajik-sl", translations=TR)["proposal"]["id"]
        sign = p.review(ADMIN, pid, "approve")["sign_id"]
        p.start_collecting(ADMIN, pid); add_samples(r, pid, ["s1", "s2", "s3"])
        p.mark_ready(ADMIN, pid); p.start_training(ADMIN, pid); p.finish_training(ADMIN, pid)
        res = p.conclude(ADMIN, pid, old_f1_before={}, old_f1_after={}, new_f1={sign: 0.4},
                         min_new_sign_f1=0.8, max_regression=0.02)
        self.assertFalse(res.passed)
        self.assertEqual(r.proposals[pid]["status"], "failed")
        self.assertNotEqual(r.signs[sign]["status"], "active")

    def test_one_video_or_one_person_is_not_ready(self):
        r, d, p = env()
        pid = p.submit(USER, gloss="HELLO", sign_language="tajik-sl", translations=TR)["proposal"]["id"]
        p.review(ADMIN, pid, "approve"); p.start_collecting(ADMIN, pid)
        add_samples(r, pid, ["s1"], per=40)
        with self.assertRaises(DomainError) as cm:
            p.mark_ready(ADMIN, pid)
        self.assertEqual(cm.exception.code, "not_ready")

    def test_permissions(self):
        r, d, p = env()
        with self.assertRaises(DomainError): p.submit(NOBODY, gloss="A", sign_language="x", translations=TR)
        pid = p.submit(USER, gloss="A", sign_language="x", translations=TR)["proposal"]["id"]
        for call in (lambda: p.review(USER, pid, "approve"), lambda: p.start_collecting(USER, pid),
                     lambda: d.add_sign(USER, gloss="B", sign_language="x", translations=TR)):
            with self.assertRaises(DomainError) as cm: call()
            self.assertEqual(cm.exception.http_status, 403)

    def test_reject_needs_reason_and_state_is_kept(self):
        r, d, p = env()
        pid = p.submit(USER, gloss="A", sign_language="x", translations=TR)["proposal"]["id"]
        with self.assertRaises(DomainError): p.review(ADMIN, pid, "reject")
        self.assertEqual(r.proposals[pid]["status"], "submitted")
        p.review(ADMIN, pid, "reject", note="not a sign")
        self.assertEqual(r.proposals[pid]["status"], "rejected")

    def test_cannot_skip_steps(self):
        r, d, p = env()
        pid = p.submit(USER, gloss="A", sign_language="x", translations=TR)["proposal"]["id"]
        with self.assertRaises(DomainError) as cm: p.start_collecting(ADMIN, pid)
        self.assertEqual(cm.exception.code, "invalid_transition")

    def test_duplicate_warning_and_user_sees_only_own(self):
        r, d, p = env()
        d.add_sign(ADMIN, gloss="HELLO", sign_language="tajik-sl", translations=TR, status="active")
        out = p.submit(USER, gloss="HI", sign_language="tajik-sl", translations=[("en", "Hello")])
        self.assertEqual(len(out["possible_duplicates"]), 1)
        p.submit(Actor("u2", ["user"]), gloss="X", sign_language="tajik-sl", translations=TR)
        self.assertEqual(len(p.list_own(USER)), 1)


class Merge(unittest.TestCase):
    def test_merge_moves_translations_and_samples(self):
        r, d, p = env()
        a = d.add_sign(ADMIN, gloss="A", sign_language="tajik-sl", translations=[("en", "one")], status="active")
        b = d.add_sign(ADMIN, gloss="B", sign_language="tajik-sl", translations=[("en", "uno")], status="active")
        r.samples.append({"sign_id": b["id"], "proposal_id": None, "signer_id": "s", "qc_passed": True, "verified": True})
        d.merge(ADMIN, b["id"], a["id"])
        self.assertEqual(r.signs[b["id"]]["status"], "merged")
        self.assertEqual(r.samples[0]["sign_id"], a["id"])
        self.assertIn(("en", "uno"), r.signs[a["id"]]["translations"])
        self.assertEqual(d.search("uno", "en")[0]["id"], a["id"])      # merged sign no longer listed

    def test_invalid_merges(self):
        r, d, p = env()
        a = d.add_sign(ADMIN, gloss="A", sign_language="tajik-sl", translations=TR)
        b = d.add_sign(ADMIN, gloss="B", sign_language="other-sl", translations=TR)
        for s, t in ((a["id"], a["id"]), (a["id"], b["id"])):
            with self.assertRaises(DomainError): d.merge(ADMIN, s, t)
        with self.assertRaises(DomainError): d.merge(USER, a["id"], b["id"])


if __name__ == "__main__":
    unittest.main()
