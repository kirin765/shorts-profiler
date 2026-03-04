from app.analysis.text_events import build_text_events


def test_text_events_merge_roles_and_time() -> None:
    raw = [
        {
            "t": 0.1,
            "text": "today only",
            "x": 50,
            "y": 1700,
            "w": 300,
            "h": 60,
            "frame_w": 1080,
            "frame_h": 1920,
        },
        {
            "t": 0.4,
            "text": "today only",
            "x": 55,
            "y": 1705,
            "w": 290,
            "h": 62,
            "frame_w": 1080,
            "frame_h": 1920,
        },
        {
            "t": 3.2,
            "text": "download now",
            "x": 800,
            "y": 1750,
            "w": 180,
            "h": 40,
            "frame_w": 1080,
            "frame_h": 1920,
        },
    ]
    events = build_text_events(raw, duration_sec=5.0)

    assert len(events) == 2
    assert events[0]["role"] in {"subtitle", "overlay", "cta"}
    assert events[1]["t0"] > events[0]["t1"]
    assert events[0]["derived"]["keywords"]


def test_text_events_empty_input_returns_empty() -> None:
    assert build_text_events([], duration_sec=3.0) == []
