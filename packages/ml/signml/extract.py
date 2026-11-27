"""Video -> landmark frames with MediaPipe Tasks. Needs model files you download once:
  hand_landmarker.task, pose_landmarker_full.task  (from the MediaPipe model page).
STATUS: written against the Tasks API; NOT executed (no model files / no real recordings offline).
Only landmarks are kept; the caller may delete the video right after extraction."""
import cv2


def _xyz(lms):
    return [[p.x, p.y, p.z] for p in lms]


def extract_frames(video_path, hand_model, pose_model, max_frames=400):
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=hand_model), num_hands=2, running_mode=vision.RunningMode.VIDEO))
    pose = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=pose_model), running_mode=vision.RunningMode.VIDEO))
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    frames, brightness, sharp, i = [], [], [], 0
    while i < max_frames:
        ok, bgr = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        brightness.append(float(gray.mean()))
        sharp.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        ts = int(1000 * i / fps) if fps else i * 33
        pr, hr = pose.detect_for_video(img, ts), hands.detect_for_video(img, ts)
        fr = {"pose": _xyz(pr.pose_landmarks[0]) if pr.pose_landmarks else None, "left_hand": None, "right_hand": None}
        for lm, hd in zip(hr.hand_landmarks, hr.handedness):
            # MediaPipe reports handedness for a mirrored image: "Left" label = person's right hand when not flipped.
            key = "right_hand" if hd[0].category_name == "Left" else "left_hand"
            fr[key] = _xyz(lm)
        frames.append(fr)
        i += 1
    cap.release()
    return frames, fps, brightness, sharp
