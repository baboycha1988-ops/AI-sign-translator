"""Upload validation for dataset samples. Mirrors the database consent rule (guard_sample_consent)."""
import hashlib

MAX_VIDEO_BYTES = 30 * 1024 * 1024


class IngestError(Exception):
    pass


def sniff_video(head: bytes):
    """Detect container by magic bytes, never by file name or client-sent content-type."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    return None


def validate_upload(head: bytes, size: int, max_bytes: int = MAX_VIDEO_BYTES) -> str:
    if size <= 0:
        raise IngestError("empty file")
    if size > max_bytes:
        raise IngestError("file too large")
    kind = sniff_video(head)
    if kind is None:
        raise IngestError("unsupported file type (expected mp4 or webm)")
    return kind


def require_live_consent(consent: dict, signer_id: str):
    if not consent or consent.get("signer_id") != signer_id:
        raise IngestError("consent does not belong to this signer")
    if consent.get("scope") != "dataset_training":
        raise IngestError("consent scope does not cover dataset training")
    if consent.get("revoked_at") is not None:
        raise IngestError("consent was revoked")


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
