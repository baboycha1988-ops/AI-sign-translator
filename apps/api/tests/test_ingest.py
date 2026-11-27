import unittest
from app.ingest import IngestError, fingerprint, require_live_consent, validate_upload

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 20


class Ingest(unittest.TestCase):
    def test_accepts_by_magic_bytes(self):
        self.assertEqual(validate_upload(MP4, 1000), "mp4")
        self.assertEqual(validate_upload(WEBM, 1000), "webm")

    def test_rejects_disguised_or_bad_files(self):
        for head, size in ((b"MZ\x90\x00" + b"\x00" * 20, 100), (b"<html>" + b"\x00" * 20, 100), (MP4, 0), (MP4, 10**9)):
            with self.assertRaises(IngestError):
                validate_upload(head, size)

    def test_consent_rules(self):
        ok = {"signer_id": "s1", "scope": "dataset_training", "revoked_at": None}
        require_live_consent(ok, "s1")
        for bad in ({**ok, "revoked_at": "2026-11-01"}, {**ok, "scope": "analytics"}, {**ok, "signer_id": "s2"}, None):
            with self.assertRaises(IngestError):
                require_live_consent(bad, "s1")

    def test_fingerprint_is_stable(self):
        self.assertEqual(fingerprint(b"abc"), fingerprint(b"abc"))
        self.assertNotEqual(fingerprint(b"abc"), fingerprint(b"abd"))


if __name__ == "__main__":
    unittest.main()
