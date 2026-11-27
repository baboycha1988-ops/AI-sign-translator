"""Landmark -> feature vector. THE SPEC: the browser (JS) must reproduce this exactly;
packages/spec/golden_features.json pins the expected output for a fixed input.

Frame = {"pose": 33x[x,y,z] | None, "left_hand": 21x[x,y,z] | None, "right_hand": 21x[x,y,z] | None}
(MediaPipe layout). Per frame:
  pose subset (nose, shoulders, elbows, wrists)     7*3          = 21
  per hand: body-relative coords (63) + wrist-relative shape (63) + presence (1) = 127, x2 = 254
  base dim = 275; with frame-to-frame velocity the model input is 550.
Body-relative = (p - mid_shoulders) / shoulder_width, so camera distance and position do not matter.
"""
import math

POSE_IDX = (0, 11, 12, 13, 14, 15, 16)
L_SHOULDER, R_SHOULDER = 11, 12
HAND_PTS = 21
BASE_DIM = len(POSE_IDX) * 3 + 2 * (HAND_PTS * 3 * 2 + 1)
FEATURE_DIM = 2 * BASE_DIM
MIN_SCALE = 1e-6


def _body_frame(pose):
    if not pose or len(pose) < 13 or pose[L_SHOULDER] is None or pose[R_SHOULDER] is None:
        return None
    l, r = pose[L_SHOULDER], pose[R_SHOULDER]
    center = [(l[i] + r[i]) / 2.0 for i in range(3)]
    scale = math.hypot(l[0] - r[0], l[1] - r[1])
    return (center, scale) if scale > MIN_SCALE else None


def frame_features(frame):
    """Returns a list of BASE_DIM floats, or None if the body cannot be located."""
    body = _body_frame(frame.get("pose"))
    if body is None:
        return None
    center, scale = body
    pose = frame["pose"]
    out = []
    for i in POSE_IDX:
        p = pose[i]
        out += [(p[k] - center[k]) / scale for k in range(3)]
    for key in ("left_hand", "right_hand"):
        hand = frame.get(key)
        if not hand or len(hand) != HAND_PTS:
            out += [0.0] * (HAND_PTS * 3 * 2) + [0.0]
            continue
        out += [(p[k] - center[k]) / scale for p in hand for k in range(3)]
        w = hand[0]
        out += [(p[k] - w[k]) / scale for p in hand for k in range(3)]
        out.append(1.0)
    return out


def sequence_features(frames):
    """List of per-frame vectors of length FEATURE_DIM (base + velocity). Lost frames become zeros."""
    base = [frame_features(f) for f in frames]
    zero = [0.0] * BASE_DIM
    seq = []
    for i, b in enumerate(base):
        cur = b if b is not None else zero
        if i > 0 and b is not None and base[i - 1] is not None:
            vel = [cur[j] - base[i - 1][j] for j in range(BASE_DIM)]
        else:
            vel = zero
        seq.append(cur + vel)
    return seq
