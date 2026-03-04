from __future__ import annotations

import re
from pathlib import Path


def _extract_keywords(text: str, limit: int = 6) -> list[str]:
    words = re.findall(r"[A-Za-z0-9]+", (text or "").lower())
    return [w for w in words if len(w) > 1][:limit]


def _intent_type(text: str) -> str:
    t = (text or "").lower()
    if "?" in t or any(t.startswith(prefix) for prefix in ("how", "what", "why", "when", "where")):
        return "question"
    if any(k in t for k in ("warning", "careful", "don't", "dont", "not", "never")):
        return "warning"
    if any(k in t for k in ("buy", "click", "download", "save", "like", "comment")):
        return "command"
    if any(ch.isdigit() for ch in t):
        return "list"
    return "statement"


def generate_speech_segments(audio_path: Path, enable_asr: bool = True) -> dict:
    if not enable_asr:
        return {
            "extensions": {},
            "warnings": ["ASR disabled by configuration."],
        }

    if not audio_path.exists():
        return {
            "extensions": {},
            "warnings": ["audio.wav missing; ASR skipped."],
        }

    try:
        from faster_whisper import WhisperModel
    except Exception:
        return {
            "extensions": {},
            "warnings": ["ASR dependency missing: install faster-whisper to enable speech analysis."],
        }

    try:
        model = WhisperModel("base", device="cpu", compute_type="int8")
        seg_iter, info = model.transcribe(str(audio_path), vad_filter=True)

        speech_segments: list[dict] = []
        keyword_counter: dict[str, int] = {}
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        speech_seconds = 0.0

        for seg in seg_iter:
            text = (getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            start = float(getattr(seg, "start", 0.0) or 0.0)
            end = float(getattr(seg, "end", start) or start)
            speech_seconds += max(0.0, end - start)
            confidence = float(getattr(seg, "avg_logprob", 0.0) or 0.0)
            keywords = _extract_keywords(text, limit=6)
            for kw in keywords:
                keyword_counter[kw] = keyword_counter.get(kw, 0) + 1

            speech_segments.append(
                {
                    "t0": round(start, 3),
                    "t1": round(end, 3),
                    "confidence_est": round(confidence, 4),
                    "keywords": keywords,
                    "intent_type": _intent_type(text),
                }
            )

        if not speech_segments:
            return {
                "extensions": {
                    "audio": {
                        "speech_ratio_est": 0.0,
                        "speech_segments": [],
                    }
                },
                "warnings": ["ASR ran but no speech-like segments were detected."],
            }

        ratio = speech_seconds / duration if duration > 0 else 0.0
        top_keywords = sorted(keyword_counter.items(), key=lambda kv: kv[1], reverse=True)
        top_keywords = [kw for kw, _ in top_keywords[:8]]
        summary = " ".join(top_keywords).strip()[:300]
        return {
            "extensions": {
                "audio": {
                    "speech_ratio_est": round(min(1.0, max(0.0, ratio)), 3),
                    "speech_segments": speech_segments,
                }
            },
            "warnings": ["ASR analysis completed."],
            "hook_spoken_summary": summary[:300] if summary else None,
        }
    except Exception as exc:
        return {
            "extensions": {},
            "warnings": [f"ASR failed and was skipped: {exc}"],
        }

