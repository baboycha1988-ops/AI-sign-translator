"""Runs with the standard library only:  python -m unittest discover -s tests -v"""
import re
import unittest
from pathlib import Path

from app.auth_service import AuthError, AuthService
from app.core import security
from app.core.lifecycle import TRANSITIONS, can_transition, evaluate_gate
from app.core.permissions import ROLE_PERMISSIONS, has_permission
from app.core.ratelimit import TokenBucket
from app.core.recognition import Thresholds, decide
from app.repo import InMemoryRepo

SECRET = "x" * 40
TH = Thresholds(min_confidence=0.8, max_distance=1.0, min_margin=0.2)
SQL = Path(__file__).resolve().parents[3] / "db" / "migrations" / "0001_initial.sql"


class Passwords(unittest.TestCase):
    def test_hash_verify(self):
        h = security.hash_password("correct horse battery")
        self.assertTrue(security.verify_password("correct horse battery", h))
        self.assertFalse(security.verify_password("wrong horse battery", h))
        self.assertNotIn("correct", h)

    def test_salted(self):
        self.assertNotEqual(security.hash_password("same-password-1"), security.hash_password("same-password-1"))

    def test_malformed_hash_is_false_not_crash(self):
        self.assertFalse(security.verify_password("x", "garbage"))

    def test_policy(self):
        self.assertTrue(security.check_password_policy("short"))
        self.assertTrue(security.check_password_policy("1234567890"))
        self.assertFalse(security.check_password_policy("long enough pass"))


class Tokens(unittest.TestCase):
    def test_roundtrip(self):
        t = security.make_access_token("u1", ["user"], SECRET)
        self.assertEqual(security.decode_access_token(t, SECRET)["sub"], "u1")

    def test_wrong_secret_expired_and_none_alg(self):
        t = security.make_access_token("u1", ["user"], SECRET)
        with self.assertRaises(security.TokenError):
            security.decode_access_token(t, "y" * 40)
        with self.assertRaises(security.TokenError):
            security.decode_access_token(security.make_access_token("u1", [], SECRET, ttl=-10), SECRET)
        import jwt
        forged = jwt.encode({"sub": "u1", "exp": 9999999999, "typ": "access"}, key="", algorithm="none")
        with self.assertRaises(security.TokenError):
            security.decode_access_token(forged, SECRET)


class OpenSet(unittest.TestCase):
    def test_confident_known_sign(self):
        d = decide({"salom": 0.93, "rahmat": 0.05, "ha": 0.02}, 0.4, TH)
        self.assertEqual((d.status, d.label), ("recognized", "salom"))

    def test_unknown_gesture_is_not_a_random_word(self):
        d = decide({"salom": 0.41, "rahmat": 0.39, "ha": 0.20}, 2.5, TH)
        self.assertEqual(d.status, "unknown")
        self.assertIsNone(d.label)

    def test_far_from_prototype_blocks_even_confident_output(self):
        d = decide({"salom": 0.97, "ha": 0.03}, 5.0, TH)
        self.assertEqual((d.status, d.label), ("unknown", None))
        self.assertIn("far_from_prototype", d.reasons)

    def test_missing_distance_fails_closed(self):
        self.assertEqual(decide({"a": 0.99, "b": 0.01}, None, TH).status, "unknown")

    def test_close_call_asks_for_clarification(self):
        d = decide({"a": 0.85, "b": 0.75}, 0.3, Thresholds(0.8, 1.0, 0.2))
        self.assertEqual(d.status, "ambiguous")
        self.assertEqual([k for k, _ in d.alternatives], ["a", "b"])

    def test_garbage_input_is_unknown(self):
        self.assertEqual(decide({}, 0.1, TH).status, "unknown")
        self.assertEqual(decide({"a": float("nan")}, 0.1, TH).status, "unknown")
        self.assertEqual(decide({"a": 1.7}, 0.1, TH).status, "unknown")


class Lifecycle(unittest.TestCase):
    def test_transitions(self):
        self.assertTrue(can_transition("draft", "submitted"))
        self.assertFalse(can_transition("draft", "published"))
        self.assertFalse(can_transition("published", "draft"))

    def test_matches_sql_trigger(self):
        sql = SQL.read_text(encoding="utf-8")
        body = sql[sql.index("ok := CASE OLD.status"):sql.index("END;\n  IF NOT ok")]
        parsed = {m.group(1): set(re.findall(r"'(\w+)'", m.group(2)))
                  for m in re.finditer(r"WHEN '(\w+)'\s+THEN NEW\.status IN \(([^)]*)\)", body)}
        expected = {k: v for k, v in TRANSITIONS.items() if v}
        self.assertEqual(parsed, expected)

    def test_gate_passes_clean_update(self):
        r = evaluate_gate(old_f1_before={"a": 0.90}, old_f1_after={"a": 0.89}, new_f1={"n": 0.85},
                          min_new_sign_f1=0.8, max_regression=0.02)
        self.assertTrue(r.passed, r.reasons)

    def test_gate_blocks_regression_weak_new_sign_and_missing_config(self):
        self.assertFalse(evaluate_gate(old_f1_before={"a": 0.9}, old_f1_after={"a": 0.8}, new_f1={},
                                       min_new_sign_f1=0.8, max_regression=0.02).passed)
        self.assertFalse(evaluate_gate(old_f1_before={}, old_f1_after={}, new_f1={"n": 0.5},
                                       min_new_sign_f1=0.8, max_regression=0.02).passed)
        self.assertFalse(evaluate_gate(old_f1_before={"a": 0.9}, old_f1_after={"a": 0.9}, new_f1={"n": 0.9},
                                       min_new_sign_f1=None, max_regression=None).passed)
        self.assertFalse(evaluate_gate(old_f1_before={"a": 0.9}, old_f1_after={}, new_f1={},
                                       min_new_sign_f1=0.8, max_regression=0.02).passed)


class Permissions(unittest.TestCase):
    def test_matches_sql_seed(self):
        sql = SQL.read_text(encoding="utf-8")
        block = sql[sql.index("FROM (VALUES\n  ('user','translate.use')"):sql.index(") AS m(role_code, perm_code)")]
        pairs = re.findall(r"\('(\w+)','([\w.]+)'\)", block)
        parsed = {}
        for role, perm in pairs:
            parsed.setdefault(role, set()).add(perm)
        self.assertEqual(parsed, ROLE_PERMISSIONS)

    def test_admin_cannot_review_private_samples_or_read_user_video(self):
        self.assertFalse(has_permission(["admin"], "samples.review_consented"))
        self.assertFalse(any("video" in p for perms in ROLE_PERMISSIONS.values() for p in perms))


class RateLimit(unittest.TestCase):
    def test_bucket(self):
        now = [0.0]
        b = TokenBucket(2, 1.0, clock=lambda: now[0])
        self.assertTrue(b.allow("k")); self.assertTrue(b.allow("k")); self.assertFalse(b.allow("k"))
        now[0] += 1.0
        self.assertTrue(b.allow("k"))
        self.assertTrue(b.allow("other"))


class Auth(unittest.TestCase):
    def setUp(self):
        self.svc = AuthService(InMemoryRepo(), SECRET, dev_auto_verify=True)

    def test_register_login_refresh_rotation_and_reuse_detection(self):
        self.svc.register("User@Example.com", "good password 1")
        pair = self.svc.login("user@example.com", "good password 1")
        pair2 = self.svc.refresh(pair.refresh_token)
        self.assertNotEqual(pair.refresh_token, pair2.refresh_token)
        with self.assertRaises(AuthError):          # old token reused -> theft assumed
            self.svc.refresh(pair.refresh_token)
        with self.assertRaises(AuthError):          # whole family is dead now
            self.svc.refresh(pair2.refresh_token)

    def test_errors(self):
        self.svc.register("a@b.tj", "good password 1")
        for bad in (lambda: self.svc.register("a@b.tj", "good password 1"),
                    lambda: self.svc.register("nope", "good password 1"),
                    lambda: self.svc.register("c@b.tj", "short"),
                    lambda: self.svc.login("a@b.tj", "wrong password!"),
                    lambda: self.svc.login("missing@b.tj", "whatever password")):
            with self.assertRaises(AuthError):
                bad()

    def test_unverified_cannot_login_and_refresh_not_stored_raw(self):
        svc = AuthService(InMemoryRepo(), SECRET, dev_auto_verify=False)
        svc.register("p@b.tj", "good password 1")
        with self.assertRaises(AuthError) as cm:
            svc.login("p@b.tj", "good password 1")
        self.assertEqual(cm.exception.code, "not_verified")
        self.svc.register("q@b.tj", "good password 1")
        p = self.svc.login("q@b.tj", "good password 1")
        self.assertNotIn(p.refresh_token, self.svc.repo.refresh)

    def test_short_secret_rejected(self):
        with self.assertRaises(ValueError):
            AuthService(InMemoryRepo(), "short")


if __name__ == "__main__":
    unittest.main()
