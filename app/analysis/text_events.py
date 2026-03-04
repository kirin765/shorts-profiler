from __future__ import annotations

import re
from collections import Counter
from typing import Any


_STOP_WORDS = {
    "the",
    "and",
    "that",
    "for",
    "with",
    "this",
    "your",
    "you",
    "or",
    "of",
    "to",
    "a",
    "in",
    "on",
    "is",
}


def _normalize_text(raw: str) -> str:
    return " ".join((raw or "").strip().lower().split())


def _extract_keywords(raw: str, max_words: int = 6) -> list[str]:
    words = re.findall(r"[A-Za-z0-9]+", _normalize_text(raw))
    cleaned: list[str] = []
    for w in words:
        if len(w) <= 1:
            continue
        if w in _STOP_WORDS:
            continue
        cleaned.append(w)
    return cleaned[:max_words]


def _text_position(det: dict[str, Any]) -> str:
    h = max(1, float(det.get("frame_h") or 1.0))
    y = float(det.get("y", 0))
    hh = float(det.get("h", 0))
    center = (y + hh / 2.0) / h
    if center <= 0.35:
        return "top"
    if center >= 0.68:
        return "bottom"
    return "middle"


def _guess_role(det: dict[str, Any], position: str) -> str:
    w = float(det.get("w", 0))
    h = float(det.get("h", 0))
    fw = max(1.0, float(det.get("frame_w") or 1.0))
    fh = max(1.0, float(det.get("frame_h") or 1.0))
    area = (w * h) / (fw * fh)
    x = float(det.get("x", 0))
    text = _normalize_text(det.get("text", ""))
    word_len = len(text)

    if position == "bottom" and word_len <= 22 and x > fw * 0.55 and area < 0.03:
        return "cta"
    if position == "bottom" and area >= 0.02 and len(text) > 6:
        return "subtitle"
    if position in {"top", "middle"} and (area > 0.03 or len(text) >= 18):
        return "overlay"
    if word_len <= 12 and position == "bottom":
        return "subtitle"
    return "overlay"


def _text_type(text: str) -> str:
    t = _normalize_text(text)
    if not t:
        return "unknown"
    if "?" in text or t.endswith("?"):
        return "question"
    if any(k in t for k in ("check this", "click", "download", "follow", "save", "like", "comment")):
        return "command"
    if any(k in t for k in ("warning", "careful", "no", "not", "don't", "stop", "forbidden")):
        return "warning"
    if any(k in t for k in ("and", "vs", "versus", "compare")):
        return "compare"
    if any(ch.isdigit() for ch in t):
        return "list"
    if len(t.split()) <= 4 and not t[:1].isupper():
        return "statement"
    return "statement"


def _density_label(chars: int, span_sec: float) -> str:
    if span_sec <= 0:
        return "low"
    density = chars / span_sec
    if density <= 12:
        return "low"
    if density <= 24:
        return "mid"
    return "high"


def _merge_key_similarity(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    left = set(a)
    right = set(b)
    inter = len(left & right)
    union = len(left | right)
    if union == 0:
        return 0.0
    return inter / union


def _keywords_from_event(det: dict[str, Any]) -> list[str]:
    return _extract_keywords(det.get("text", ""))


def build_text_events(raw_detections: list[dict[str, Any]], duration_sec: float, max_events: int = 40) -> list[dict[str, Any]]:
    if not raw_detections:
        return []

    prepared: list[dict[str, Any]] = []
    for det in sorted(raw_detections, key=lambda row: float(row.get("t", 0.0))):
        text = _normalize_text(det.get("text", ""))
        if not text:
            continue

        fw = max(1.0, float(det.get("frame_w") or 1.0))
        fh = max(1.0, float(det.get("frame_h") or 1.0))
        w = max(0.0, float(det.get("w") or 0.0))
        h = max(0.0, float(det.get("h") or 0.0))
        keywords = _keywords_from_event(det)
        if not keywords and len(text) < 6:
            continue

        position = _text_position(det)
        role = _guess_role(det, position)
        size_est = min(1.0, (w * h) / (fw * fh))
        char_len_est = max(1, len("".join(keywords)) if keywords else len(text))
        span = 0.35
        density = _density_label(char_len_est, span)
        style_tags = []
        if size_est >= 0.04:
            style_tags.append("large_est")
        if size_est <= 0.01:
            style_tags.append("compact_est")
        if position == "bottom":
            style_tags.append("bottom_bias")

        prepared.append(
            {
                "t0": max(0.0, float(det.get("t", 0.0)) - 0.05),
                "t1": float(det.get("t", 0.0)) + 0.3,
                "role": role,
                "position": position,
                "size_est": round(size_est, 4),
                "style_tags": style_tags or ["text_est"],
                "derived": {
                    "keywords": keywords[:8],
                    "has_number": any(ch.isdigit() for ch in text),
                    "text_type": _text_type(det.get("text", "")),
                    "char_len_est": int(char_len_est),
                    "density_est": density,
                },
            }
        )

    if not prepared:
        return []

    merged: list[dict[str, Any]] = []
    for event in prepared:
        if len(merged) >= max_events:
            break

        if not merged:
            merged.append(event)
            continue

        prev = merged[-1]
        prev_kw = prev["derived"]["keywords"]
        cur_kw = event["derived"]["keywords"]
        temporal_gap = event["t0"] - prev["t1"]

        can_merge = (
            prev["role"] == event["role"]
            and prev["position"] == event["position"]
            and temporal_gap <= 0.6
            and _merge_key_similarity(prev_kw, cur_kw) >= 0.2
        )

        if can_merge:
            prev["t1"] = max(prev["t1"], event["t1"])
            merged_keywords = (prev_kw or []) + (cur_kw or [])
            # unique keep order
            seen: set[str] = set()
            prev["derived"]["keywords"] = [k for k in merged_keywords if not (k in seen or seen.add(k))]
            prev["derived"]["char_len_est"] = max(int(prev["derived"].get("char_len_est", 0)), int(event["derived"].get("char_len_est", 0)))
            prev["derived"]["density_est"] = _density_label(
                int(prev["derived"]["char_len_est"]),
                max(0.3, prev["t1"] - prev["t0"]),
            )
            if prev["derived"]["text_type"] != event["derived"]["text_type"]:
                prev["derived"]["text_type"] = "unknown"
        else:
            merged.append(event)

    for event in merged:
        event["t0"] = round(max(0.0, event["t0"]), 3)
        event["t1"] = round(min(max(0.0, float(duration_sec)), event["t1"]), 3)

    return merged


def summarize_hook_from_events(events: list[dict[str, Any]], max_len: int = 500) -> str | None:
    if not events:
        return None

    parts: list[str] = []
    seen: set[str] = set()
    for event in events:
        if event.get("t0", 0.0) > 3.0:
            break
        kw = event.get("derived", {}).get("keywords", [])
        text_type = str(event.get("derived", {}).get("text_type") or "statement")
        item = f"{text_type}:{' '.join(kw[:2])}"
        item = item.strip()
        if item and item not in seen:
            parts.append(item)
            seen.add(item)
        if sum(len(p) + 1 for p in parts) > max_len:
            break

    if not parts:
        return None

    hook = " | ".join(parts)
    return hook[:max_len]


def build_position_stats(events: list[dict[str, Any]]) -> tuple[dict[str, int], bool, int, float]:
    if not events:
        return {"top": 0, "middle": 0, "bottom": 0}, False, 0, 0.0

    counter = Counter(event.get("position", "middle") for event in events)
    total = sum(counter.values())
    present = total > 0
    total_chars = sum(int(event.get("derived", {}).get("char_len_est", 0)) for event in events)
    density_ratio = total_chars / max(1.0, total)
    return {
        "top": counter.get("top", 0),
        "middle": counter.get("middle", 0),
        "bottom": counter.get("bottom", 0),
    }, present, total_chars, density_ratio
