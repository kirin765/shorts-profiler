import random
from typing import Any, Dict


SORA_VISUAL = [
    "Use a clean vertical frame composition, stable subject framing, and high-contrast lighting.",
    "Apply controlled punchy cuts with rhythm following scene energy. Keep background minimal.",
    "Prioritize readable compositions with quick subject movement and restrained camera zoom events.",
]

SORA_EDITING = [
    "Cut every {cuts_per_10s:.1f} beats per 10 seconds, allowing micro-reactions between cuts.",
    "Use alternating close-up and medium shots to emphasize hook and payoff points.",
    "Keep transition style direct (hard cuts) with no heavy dissolve effects.",
]

SEEDANCE_BEAT_STYLE = [
    "{label} from {start:.1f}s to {end:.1f}s with {action_desc}.",
    "{label} block [{start:.1f}-{end:.1f}] should deliver: {action_desc}.",
    "Timeline: {label} at {start:.1f}-{end:.1f}, maintain {action_desc}.",
]

SCRIPT_OPENERS = [
    "문제 제기 -> 핵심 장면 -> 해결/팁 -> 마무리 콜 투 액션",
    "공감형 질문으로 시작하고 짧은 데모로 진행, 마지막엔 실행 행동 유도",
    "숫자/단계 중심으로 정돈된 말풍선형 대본",
]

CTA_TEMPLATES = [
    "마무리는 공통 CTA로 정리: \"지금 바로 따라 해보세요.\"",
    "마무리는 공유형 CTA로 정리: \"좋으면 저장하고 다음 영상을 위해 북마크하세요.\"",
    "마무리는 행동형 CTA로 정리: \"함께 도전해보는 건 어떨까요?\"",
]


def _safe_hook_type(tokens: dict[str, Any]) -> str:
    return str((tokens.get("hook") or {}).get("hook_type") or "other")


def _format_beat_list(beats: list[dict[str, Any]]) -> str:
    lines = []
    for beat in beats:
        start, end = beat.get("t", [0, 0])
        lines.append(f"- {beat.get('label')}: {start:.1f}-{end:.1f}")
    return "\n".join(lines)


def build_sora_prompt(tokens: dict[str, Any]) -> str:
    editing = tokens.get("editing", {})
    visual = tokens.get("visual", {})
    audio = tokens.get("audio", {})
    subtitle = tokens.get("subtitle", {})

    cuts = float(editing.get("cuts_per_10s", 0) or 0)
    duration = float(tokens.get("duration_sec", 0) or 0)

    text = """TITLE: Vertical short-form recreation draft\n"""
    text += f"FORMAT: vertical, duration {duration:.1f}s\n"
    text += f"VISUAL: {random.choice(SORA_VISUAL)} Subject framing: face presence ratio {visual.get('face_presence_ratio_est', 0):.2f}.\n"
    text += "BACKGROUND: " + str(visual.get("background_complexity", "mid")) + "\n"
    text += f"EDITING: {random.choice(SORA_EDITING).format(cuts_per_10s=cuts)} Zoom events: {editing.get('zoom_events_est', 0)}\n"
    text += f"TEXT OVERLAY: density {subtitle.get('density', 'mid')}, style {', '.join(subtitle.get('style_tags', [])) or 'minimal'}\n"
    text += f"AUDIO: BPM {audio.get('bpm_est', 0)} (est), energy {audio.get('energy_curve', 'flat')}\n"
    text += "SCRIPT: concise, original Korean narration aligned with beat transitions.\n"
    text += f"HOOK type: {_safe_hook_type(tokens)}\n"
    text += f"CTA: {random.choice(CTA_TEMPLATES)}"
    return text


def build_seedance_prompt(tokens: dict[str, Any]) -> str:
    beats = tokens.get("structure", {}).get("beats", [])
    duration = float(tokens.get("duration_sec", 0) or 0)
    lines = ["Seedance Beat Sheet", f"Duration: {duration:.1f}s", ""]
    for beat in beats:
        start, end = beat.get("t", [0, 0])
        label = beat.get("label", "")
        action_desc = "fast factual montage"
        if label == "HOOK":
            action_desc = "attention grab and tension setup"
        elif label == "CTA":
            action_desc = "clear generic CTA with spacing"
        line = random.choice(SEEDANCE_BEAT_STYLE).format(
            label=label,
            start=float(start),
            end=float(end),
            action_desc=action_desc,
        )
        lines.append(line)

    lines.append("\nTEXT: avoid exact quoted text, use generic rewritten on-screen copy.")
    lines.append(CTA_TEMPLATES[0])
    return "\n".join(lines)


def build_script_prompt(tokens: dict[str, Any]) -> str:
    editing = tokens.get("editing", {})
    duration = float(tokens.get("duration_sec", 0) or 0)
    beats = tokens.get("structure", {}).get("beats", [])

    lines = [
        "스크립트/자막 플랜 (원문 텍스트 복제 없음)",
        f"전체 길이: {duration:.1f}s",
        f"컷 밀도: 10초당 {float(editing.get('cuts_per_10s', 0) or 0):.1f}",
        "\n",
    ]

    for beat in beats:
        start, end = beat.get("t", [0, 0])
        label = beat.get("label", "")
        opener = random.choice(SCRIPT_OPENERS)
        lines.append(f"[{start:.1f}-{end:.1f}] {label}: {opener}")

    lines.append("\n" + random.choice(CTA_TEMPLATES))
    return "\n".join(lines)


def build_prompts(tokens: dict[str, Any], target: str) -> Dict[str, str]:
    if target == "sora":
        return {"sora": build_sora_prompt(tokens)}
    if target == "seedance":
        return {"seedance": build_seedance_prompt(tokens)}
    if target == "script":
        return {"script": build_script_prompt(tokens)}

    return {
        "sora": build_sora_prompt(tokens),
        "seedance": build_seedance_prompt(tokens),
        "script": build_script_prompt(tokens),
    }
