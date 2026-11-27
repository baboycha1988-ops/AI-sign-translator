"""Automatic quality control of a recorded sample. Thresholds are PROVISIONAL defaults:
they live in system_settings later and are tuned on real recordings, not claimed correct."""
from dataclasses import dataclass, field

from .features import L_SHOULDER, R_SHOULDER, frame_features


@dataclass
class QCConfig:
    min_ms: int = 500
    max_ms: int = 6000
    min_fps: float = 15.0
    min_body_ratio: float = 0.9
    min_hands_ratio: float = 0.5
    min_motion: float = 0.002        # mean per-frame wrist displacement in shoulder-widths
    brightness_range: tuple = (40.0, 220.0)   # mean luma 0..255, if provided
    min_sharpness: float = 20.0               # variance of Laplacian, if provided


@dataclass
class QCResult:
    passed: bool
    metrics: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    segment: tuple = None            # (first_frame, last_frame) with hands visible: suggested trim


def _hands(frame):
    return bool(frame and (frame.get("left_hand") or frame.get("right_hand")))


def qc_sample(frames, *, fps, brightness=None, sharpness=None, cfg=QCConfig()):
    n = len(frames)
    duration_ms = int(1000 * n / fps) if fps and fps > 0 else 0
    reasons = []
    if n == 0 or not fps or fps <= 0:
        return QCResult(False, {"frames": n}, ["empty recording or unknown fps"])
    body = sum(frame_features(f) is not None for f in frames) / n
    hands_idx = [i for i, f in enumerate(frames) if _hands(f)]
    hands = len(hands_idx) / n
    # motion of the right-then-left wrist in body-relative units
    pts = []
    for f in frames:
        fe = frame_features(f)
        if fe is not None:
            pts.append(fe[5 * 3:5 * 3 + 2] + fe[6 * 3:6 * 3 + 2])      # pose wrists (indices 15, 16 -> slots 5, 6)
    motion = (sum(sum(abs(a - b) for a, b in zip(p, q)) / 4 for p, q in zip(pts, pts[1:])) / (len(pts) - 1)) if len(pts) > 1 else 0.0
    m = {"frames": n, "duration_ms": duration_ms, "fps": fps, "body_ratio": round(body, 3),
         "hands_visible_ratio": round(hands, 3), "motion": round(motion, 5)}
    if duration_ms < cfg.min_ms: reasons.append("too short")
    if duration_ms > cfg.max_ms: reasons.append("too long")
    if fps < cfg.min_fps: reasons.append("frame rate too low")
    if body < cfg.min_body_ratio: reasons.append("shoulders not visible in enough frames")
    if hands < cfg.min_hands_ratio: reasons.append("hands not visible in enough frames")
    if motion < cfg.min_motion: reasons.append("no movement (static pose)")
    if brightness:
        mean_b = sum(brightness) / len(brightness); m["brightness"] = round(mean_b, 1)
        if not cfg.brightness_range[0] <= mean_b <= cfg.brightness_range[1]:
            reasons.append("lighting too dark or too bright")
    if sharpness:
        mean_s = sum(sharpness) / len(sharpness); m["sharpness"] = round(mean_s, 1)
        if mean_s < cfg.min_sharpness: reasons.append("image too blurry")
    seg = (hands_idx[0], hands_idx[-1]) if hands_idx else None
    return QCResult(not reasons, m, reasons, seg)
