"""HTTP-layer tests. Need:  pip install -r requirements.txt   then   python -m unittest discover -s tests -v
Skipped automatically when FastAPI is not installed."""
import unittest

try:
    from fastapi.testclient import TestClient
    from app.main import create_app
    HAVE = True
except Exception:  # pragma: no cover
    HAVE = False

SECRET = "s" * 40


@unittest.skipUnless(HAVE, "fastapi/httpx not installed")
class Api(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(create_app(jwt_secret=SECRET, dev_auto_verify=True))

    def _login(self, email="a@b.tj", pw="good password 1"):
        self.c.post("/api/auth/register", json={"email": email, "password": pw})
        return self.c.post("/api/auth/login", json={"email": email, "password": pw}).json()

    def test_health_and_headers(self):
        r = self.c.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_register_login_me(self):
        t = self._login()
        r = self.c.get("/api/users/me", headers={"Authorization": f"Bearer {t['access_token']}"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["email"], "a@b.tj")
        self.assertNotIn("password", r.text)

    def test_me_requires_valid_token(self):
        self.assertEqual(self.c.get("/api/users/me").status_code, 401)
        self.assertEqual(self.c.get("/api/users/me", headers={"Authorization": "Bearer junk"}).status_code, 401)

    def test_duplicate_and_weak_password(self):
        self._login()
        r = self.c.post("/api/auth/register", json={"email": "a@b.tj", "password": "good password 1"})
        self.assertEqual(r.status_code, 409)
        r = self.c.post("/api/auth/register", json={"email": "z@b.tj", "password": "short"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "weak_password")

    def test_wrong_password_is_401_without_hint(self):
        self._login()
        r = self.c.post("/api/auth/login", json={"email": "a@b.tj", "password": "bad password 99"})
        self.assertEqual(r.status_code, 401)
        r2 = self.c.post("/api/auth/login", json={"email": "nobody@b.tj", "password": "bad password 99"})
        self.assertEqual(r.json(), r2.json())

    def test_refresh_rotation_and_reuse(self):
        t = self._login()
        t2 = self.c.post("/api/auth/refresh", json={"refresh_token": t["refresh_token"]})
        self.assertEqual(t2.status_code, 200)
        again = self.c.post("/api/auth/refresh", json={"refresh_token": t["refresh_token"]})
        self.assertEqual(again.status_code, 401)

    def test_regular_user_cannot_use_admin_endpoint(self):
        t = self._login()
        r = self.c.get("/api/admin/whoami", headers={"Authorization": f"Bearer {t['access_token']}"})
        self.assertEqual(r.status_code, 403)

    def test_rate_limit_on_auth(self):
        c = TestClient(create_app(jwt_secret=SECRET, dev_auto_verify=True, auth_rate=(3, 0.0001)))
        codes = [c.post("/api/auth/login", json={"email": "x@b.tj", "password": "whatever pass"}).status_code
                 for _ in range(5)]
        self.assertIn(429, codes)


if __name__ == "__main__":
    unittest.main()
