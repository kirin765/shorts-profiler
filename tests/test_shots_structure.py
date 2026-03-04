from pathlib import Path

import pytest

from app.analysis import shots
from app.core import media


def test_build_shots_from_scene_detect(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"")

    monkeypatch.setattr(media, "scene_detector_with_pyscenedetect", lambda _video_path, _duration: [1.2, 2.8, 4.1])

    result = shots.build_shots(video, duration_sec=5.0, frame_fallback_interval=0.5)

    assert len(result) == 4
    assert result[0]["shot_id"] == 0
    assert result[0]["keyframes"]
    assert result[-1]["t1"] == 5.0


def test_build_shots_fallback_hist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"")

    monkeypatch.setattr(media, "scene_detector_with_pyscenedetect", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(media, "detect_cuts_hist", lambda _frames: [1, 3, 5])

    result = shots.build_shots(video, duration_sec=6.0, frame_fallback_interval=0.5)

    assert result
    assert result[-1]["t1"] == 6.0
    assert {shot["source"] for shot in result}.issubset({"hist", "scenedetect"})
