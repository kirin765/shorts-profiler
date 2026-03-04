import json
import shutil
import subprocess
from pathlib import Path
from statistics import median
import shlex
from typing import Any
from urllib.parse import urlparse

import cv2
import numpy as np
import pytesseract

from app.core.config import settings


def _run_cmd(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def cleanup_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _parse_ytdlp_args(extra_args: str) -> list[str]:
    return shlex.split(extra_args.strip()) if extra_args else []


def _is_tiktok_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return host == "tiktok.com" or host == "www.tiktok.com" or host == "vm.tiktok.com" or host.endswith(".tiktok.com")


def _build_ytdlp_base_cmd(output_path: Path, url: str) -> list[list[str]]:
    output = str(output_path.with_suffix(""))
    base = [
        "yt-dlp",
        "--merge-output-format",
        "mp4",
        "-o",
        f"{output}.%(ext)s",
        url,
    ]

    ytdlp_args = _parse_ytdlp_args(settings.yt_dlp_args)
    base[1:1] = ytdlp_args

    attempts = [base]

    if _is_tiktok_url(url):
        attempts.append(
            [
                "yt-dlp",
                *ytdlp_args,
                "--extractor-args",
                "tiktok:api_hostname=api16-h2.tiktokv.com",
                "--merge-output-format",
                "mp4",
                "-o",
                f"{output}.%(ext)s",
                url,
            ]
        )
        attempts.append(
            [
                "yt-dlp",
                *ytdlp_args,
                "--extractor-args",
                "tiktok:api_hostname=api22-h2.tiktokv.com",
                "--merge-output-format",
                "mp4",
                "-o",
                f"{output}.%(ext)s",
                url,
            ]
        )
    return attempts


def download_video_from_url_with_ytdlp(url: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_base = str(output_path.with_suffix(""))
    attempts = _build_ytdlp_base_cmd(output_path, url)

    last_err = ""
    for attempt_idx, cmd in enumerate(attempts, start=1):
        # remove stale outputs for retries
        for stale in output_path.parent.glob(f"{output_path.stem}.*"):
            if stale.is_file():
                stale.unlink(missing_ok=True)

        rc, out, err = _run_cmd(cmd)
        if rc == 0:
            break
        last_err = err.strip() or out.strip()
        if attempt_idx < len(attempts):
            continue
        raise RuntimeError(f"yt-dlp failed: {last_err}")

    output_file = Path(f"{output_base}.mp4")
    if output_file.exists():
        return output_file

    # fallback for odd ext if remux not applied
    candidates = sorted(output_path.parent.glob(output_path.stem + ".*"))
    if not candidates:
        raise RuntimeError("yt-dlp finished but output file not found")

    downloaded = candidates[0]
    if downloaded.suffix.lower() != ".mp4":
        converted = output_file
        try:
            shutil.move(str(downloaded), converted)
            return converted
        except Exception as e:
            raise RuntimeError(f"unexpected output file extension: {downloaded.name}") from e
    return downloaded


def ffprobe_info(video_path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,width,height,duration",
        "-of",
        "json",
        str(video_path),
    ]
    rc, out, err = _run_cmd(cmd)
    if rc != 0:
        raise RuntimeError(f"ffprobe failed: {err.strip()}")

    payload = json.loads(out)
    streams = payload.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width = video_stream.get("width")
    height = video_stream.get("height")
    duration = video_stream.get("duration") or 0.0

    if isinstance(duration, str):
        duration_f = float(duration)
    else:
        duration_f = float(duration or 0.0)

    return {
        "width": int(width) if width else None,
        "height": int(height) if height else None,
        "duration_sec": duration_f,
        "has_audio": audio_stream is not None,
    }


def sample_frames(video_path: Path, out_dir: Path, interval_sec: float, max_seconds: float | None = None) -> list[Path]:
    ensure_dir(out_dir)
    pattern = str(out_dir / "frame_%05d.jpg")
    vf = f"fps=1/{interval_sec}"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        vf,
    ]
    if max_seconds:
        cmd += ["-t", f"{max_seconds}"]
    cmd += [pattern]

    rc, _, err = _run_cmd(cmd)
    if rc != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {err.strip()}")

    return sorted(out_dir.glob("frame_*.jpg"))


def sample_frame_at_timestamp(video_path: Path, out_file: Path, timestamp_sec: float) -> Path:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(max(0.0, float(timestamp_sec))),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_file),
    ]
    rc, _, err = _run_cmd(cmd)
    if rc != 0 or not out_file.exists():
        raise RuntimeError(f"ffmpeg frame capture failed at t={timestamp_sec}: {err.strip()}")

    return out_file


def sample_frames_at_timestamps(video_path: Path, out_dir: Path, timestamps: list[float]) -> list[tuple[Path, float]]:
    ensure_dir(out_dir)
    sampled: list[tuple[Path, float]] = []
    seen: set[float] = set()
    for idx, raw_ts in enumerate(sorted(set(float(t) for t in timestamps))):
        ts = round(max(0.0, raw_ts), 3)
        if ts in seen:
            continue
        seen.add(ts)
        out_file = out_dir / f"frame_{idx:05d}.jpg"
        try:
            path = sample_frame_at_timestamp(video_path, out_file, ts)
        except Exception:
            continue
        sampled.append((path, ts))
    return sampled


def _parse_tesseract_text_box_results(frame: Any, conf_threshold: float = 45.0) -> list[dict[str, Any]]:
    data = pytesseract.image_to_data(
        frame,
        output_type=pytesseract.Output.DICT,
        config="--psm 6",
    )

    h_img, w_img = frame.shape[:2]
    n = len(data.get("text", []))
    out: list[dict[str, Any]] = []
    for i in range(n):
        text = str(data["text"][i] or "").strip()
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1

        if not text or conf < conf_threshold:
            continue

        x = int(data["left"][i] or 0)
        y = int(data["top"][i] or 0)
        w = int(data["width"][i] or 0)
        h = int(data["height"][i] or 0)
        if w <= 0 or h <= 0:
            continue

        norm = " ".join(text.split())
        if len(norm) < 2:
            continue

        out.append(
            {
                "text": norm,
                "conf": conf,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "frame_w": w_img,
                "frame_h": h_img,
            }
        )
    return out


def extract_text_events_from_frames(
    frames: list[tuple[Path, float]],
    conf_threshold: float = 45.0,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for frame_path, t in frames:
        img = cv2.imread(str(frame_path))
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for det in _parse_tesseract_text_box_results(gray, conf_threshold):
            record = {"t": float(t), **det}
            out.append(record)

    out.sort(key=lambda row: row["t"])
    return out


def detect_cuts_hist(frames: list[Path]) -> list[float]:
    if len(frames) < 2:
        return []

    cuts: list[float] = []
    prev = cv2.imread(str(frames[0]))
    prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
    for idx in range(1, len(frames)):
        img = cv2.imread(str(frames[idx]))
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if gray.shape != prev_gray.shape:
            prev_gray = cv2.resize(prev_gray, (gray.shape[1], gray.shape[0]))
        diff = np.mean(cv2.absdiff(gray, prev_gray).astype(np.float32))
        if diff > 22:
            # frame index corresponds to roughly time slot index*interval
            cuts.append(float(idx))
        prev_gray = gray
    return cuts


def scene_detector_with_pyscenedetect(video_path: Path, duration: float) -> list[float]:
    try:
        from scenedetect import detect
        from scenedetect.detectors import ContentDetector

        scenes = detect(str(video_path), ContentDetector(threshold=30.0))
        cuts: list[float] = []
        for scene in scenes:
            cuts.append(float(scene[0].get_seconds()))
        # filter any trailing value and clip by duration
        return [c for c in cuts if 0 < c < duration]
    except Exception:
        return []


def estimate_cuts(video_path: Path, frames: list[Path], duration: float) -> list[float]:
    cuts = scene_detector_with_pyscenedetect(video_path, duration)
    if cuts:
        return cuts
    return detect_cuts_hist(frames)


def extract_text_frames(
    frames: list[Path], max_text_chars: int = 500
) -> tuple[int, dict[str, float], bool, list[str], str | None]:
    if not frames:
        return 0, {"top": 0.0, "middle": 0.0, "bottom": 0.0}, False, []

    total_chars = 0
    present_count = 0
    position_hits = {"top": 0, "middle": 0, "bottom": 0}
    subtitle_texts: list[str] = []
    hook_candidates: list[str] = []

    for frame_path in frames:
        img = cv2.imread(str(frame_path))
        if img is None:
            continue

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        data = pytesseract.image_to_data(
            gray,
            output_type=pytesseract.Output.DICT,
            config="--psm 6",
        )

        n = len(data.get("text", []))
        for i in range(n):
            text = str(data["text"][i] or "").strip()
            conf = data["conf"][i]
            try:
                conf_f = float(conf)
            except Exception:
                conf_f = -1

            if not text or conf_f < 45:
                continue

            x = int(data["left"][i] or 0)
            y = int(data["top"][i] or 0)
            w = int(data["width"][i] or 0)
            h = int(data["height"][i] or 0)
            if w <= 0 or h <= 0:
                continue

            center_y = y + h / 2
            h_img = gray.shape[0]
            if center_y < h_img * 0.35:
                position_hits["top"] += 1
            elif center_y > h_img * 0.68:
                position_hits["bottom"] += 1
            else:
                position_hits["middle"] += 1

            total_chars += len(text)
            present_count += 1
            norm = " ".join(text.split())
            if len(norm) >= 4:
                subtitle_texts.append(norm)
                hook_candidates.append(norm)

    total_frames = max(len(frames), 1)
    position_ratio = {
        k: v / total_frames for k, v in position_hits.items()
    }
    # remove duplicates while preserving order
    deduped = list(dict.fromkeys(hook_candidates))
    hook_text = " | ".join(deduped) if deduped else None
    if hook_text and len(hook_text) > max_text_chars:
        hook_text = hook_text[:max_text_chars]
    return total_chars, position_ratio, present_count > 0, subtitle_texts, hook_text


def estimate_face_presence(frames: list[Path]) -> tuple[float, float]:
    xml_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_detector = cv2.CascadeClassifier(xml_path)
    if face_detector.empty():
        return 0.0, 0.0

    ratio = 0
    close_up_hits = 0
    usable = 0

    for frame in frames[:80]:
        img = cv2.imread(str(frame))
        if img is None:
            continue

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = face_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        usable += 1

        if len(faces) > 0:
            ratio += 1
            _, _, w, h = faces[0]
            face_area = w * h
            img_area = img.shape[0] * img.shape[1]
            if img_area > 0 and (face_area / img_area) > 0.2:
                close_up_hits += 1

    if usable == 0:
        return 0.0, 0.0

    return ratio / usable, close_up_hits / usable


def estimate_background_complexity(frames: list[Path]) -> str:
    if not frames:
        return "unknown"

    entropies: list[float] = []
    for frame in frames[:40]:
        img = cv2.imread(str(frame))
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
        p = hist / (np.sum(hist) + 1e-8)
        entropy = -float(np.sum(p * np.log2(p + 1e-8)))
        entropies.append(entropy)

    if not entropies:
        return "unknown"

    e = median(entropies)
    if e < 3.8:
        return "low"
    if e < 5.2:
        return "mid"
    return "high"


def extract_audio_metrics(video_path: Path, out_wav: Path) -> tuple[bool, int, str, float]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        str(out_wav),
    ]
    rc, _, err = _run_cmd(cmd)
    if rc != 0 or not out_wav.exists():
        return False, 0, "unknown", 0.0

    import wave

    with wave.open(str(out_wav), "rb") as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        frame_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width == 2:
        dtype = np.int16
    elif sample_width == 4:
        dtype = np.int32
    else:
        dtype = np.int16

    audio = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1)

    if len(audio) == 0:
        return False, 0, "unknown", 0.0

    audio = audio / (np.max(np.abs(audio)) + 1e-8)
    frame_len = max(int(frame_rate * 0.2), 1)
    rms = []
    for i in range(0, len(audio), frame_len):
        chunk = audio[i : i + frame_len]
        if len(chunk) == 0:
            continue
        rms.append(float(np.sqrt(np.mean(np.square(chunk)))))

    if not rms:
        return True, 0, "unknown", 0.0

    rms_arr = np.array(rms)
    peak = float(np.max(rms_arr) + 1e-8)
    silence_ratio = float(np.mean(rms_arr < peak * 0.03))

    split = np.array_split(rms_arr, 3)
    first = float(np.mean(split[0])) if len(split) > 0 else 0.0
    mid = float(np.mean(split[1])) if len(split) > 1 else 0.0
    end = float(np.mean(split[2])) if len(split) > 2 else 0.0

    energy_curve = "flat"
    if first >= mid * 1.25 and first >= end * 1.1:
        energy_curve = "front_loaded"
    elif end >= first * 1.25 and end >= mid * 1.1:
        energy_curve = "end_loaded"

    bpm_est = int((first + mid + end) * 0)
    return True, bpm_est, energy_curve, float(silence_ratio)


def build_beat_structure(duration_sec: float, cuts_per_10s: float) -> list[dict[str, Any]]:
    duration_sec = max(duration_sec, 1.0)
    hook_end = min(2.0, max(0.8, duration_sec * 0.1))
    cta_start = max(0.75 * duration_sec, duration_sec - max(2.5, duration_sec * 0.15))

    return [
        {"t": [0.0, round(hook_end, 2)], "label": "HOOK"},
        {"t": [round(hook_end, 2), round(ca(0.25 * duration_sec + hook_end, duration_sec), 2)], "label": "EXPLAIN"},
        {"t": [round(ca(0.25 * duration_sec + hook_end, duration_sec), 2), round(cta_start, 2)], "label": "STEPS"},
        {"t": [round(cta_start, 2), round(duration_sec, 2)], "label": "CTA"},
    ]


def ca(value: float, duration_sec: float) -> float:
    return min(value, duration_sec)
