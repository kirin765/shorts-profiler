from __future__ import annotations

import tempfile
from pathlib import Path

from app.core import media


def _as_float(value: float, fallback: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(fallback)


def _clamp(value: float, lower: float, upper: float) -> float:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def _normalize_boundaries(duration_sec: float, cuts: list[float]) -> list[float]:
    duration_sec = max(0.0, _as_float(duration_sec, 0.0))
    if duration_sec <= 0:
        return [0.0]

    boundaries = [0.0]
    for c in cuts:
        c = _as_float(c, 0.0)
        if 0 < c < duration_sec:
            boundaries.append(c)
    boundaries.append(duration_sec)
    normalized: list[float] = []
    for b in sorted(set(round(float(x), 3) for x in boundaries)):
        b = _clamp(b, 0.0, duration_sec)
        if not normalized or b > normalized[-1]:
            normalized.append(b)

    if len(normalized) == 1:
        normalized.append(duration_sec)

    return normalized


def _build_shot_keyframes(t0: float, t1: float, eps: float = 0.1, min_shot_len: float = 0.25) -> list[float]:
    length = max(0.0, t1 - t0)
    if length <= 0:
        return []
    points: list[float] = []

    if length <= min_shot_len:
        points.append(t0 + min(eps, length * 0.4))
    else:
        points.extend(
            [
                t0 + min(eps, length * 0.25),
                (t0 + t1) / 2,
                t1 - min(eps, length * 0.25),
            ]
        )

    # remove dupes and out-of-range
    out: list[float] = []
    for p in sorted(set(round(float(x), 3) for x in points)):
        if p <= t0 or p >= t1:
            continue
        out.append(p)
        if len(out) >= 3:
            break
    return out


def _scene_detect_with_fallback(video_path: Path, duration_sec: float, fallback_interval: float) -> tuple[list[float], str]:
    scene_cuts = media.scene_detector_with_pyscenedetect(video_path, duration_sec)
    if scene_cuts:
        return sorted(scene_cuts), "scenedetect"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        frames = media.sample_frames(video_path, tmp_dir / "frames", interval_sec=fallback_interval)
        if not frames:
            return [duration_sec], "hist"
        hist_positions = media.detect_cuts_hist(frames)
        cuts = [p * fallback_interval for p in sorted(set(hist_positions))]
        cuts = [c for c in cuts if 0 < c < duration_sec]
        return cuts, "hist"


def build_shots(
    video_path: Path,
    duration_sec: float,
    frame_fallback_interval: float = 0.5,
) -> list[dict[str, object]]:
    duration_sec = _as_float(duration_sec, 0.0)
    if duration_sec <= 0:
        return []

    cuts, source = _scene_detect_with_fallback(
        video_path=video_path,
        duration_sec=duration_sec,
        fallback_interval=frame_fallback_interval,
    )

    boundaries = _normalize_boundaries(duration_sec, list(cuts))
    shots: list[dict[str, object]] = []

    for shot_id, (t0, t1) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        if t1 <= t0:
            continue
        keyframes = _build_shot_keyframes(t0, t1, eps=0.1, min_shot_len=0.25)
        shots.append(
            {
                "shot_id": shot_id,
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "keyframes": [round(float(k), 3) for k in keyframes],
                "source": source,
            }
        )

    return shots

