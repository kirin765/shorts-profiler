from __future__ import annotations

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
    "Problem statement -> core scene -> fix/tip -> CTA closeout.",
    "Start with a relatable hook and run short demonstrations, end with clear action guidance.",
    "Number-driven, step-based outline with concise language.",
]

CTA_TEMPLATES = [
    "CTA: End with a reusable action prompt for viewers.",
    "CTA: End with a save/share reminder and next-step cue.",
    "CTA: End with a polite, non-branded challenge invite.",
]

GENERIC_SECTIONS = [
    "Start with a short emotional hook before revealing value.",
    "Keep rhythm-driven transitions tied to visual changes.",
    "Prioritize safe, non-identifying language and avoid verbatim text reuse.",
]

GENERIC_CTA_HINTS = [
    "CTA: end with one clear micro-action (save, follow, or comment).",
    "CTA: close with a simple generic ask and one timing cue.",
    "CTA: keep it short and non-branded with one final directive.",
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

    text = """TITLE: Vertical short-form recreation draft
"""
    text += f"FORMAT: vertical, duration {duration:.1f}s\n"
    text += f"VISUAL: {random.choice(SORA_VISUAL)} Subject framing ratio {visual.get('face_presence_ratio_est', 0):.2f}.\n"
    text += "BACKGROUND: " + str(visual.get("background_complexity", "mid")) + "\n"
    text += f"EDITING: {random.choice(SORA_EDITING).format(cuts_per_10s=cuts)} Zoom events: {editing.get('zoom_events_est', 0)}\n"
    text += f"TEXT OVERLAY: density {subtitle.get('density', 'mid')}, style {', '.join(subtitle.get('style_tags', [])) or 'minimal'}\n"
    text += f"AUDIO: BPM {audio.get('bpm_est', 0)} (est), energy {audio.get('energy_curve', 'flat')}\n"
    text += "SCRIPT: concise, original narration aligned with beat transitions.\n"
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

    lines.append("\nTEXT: avoid verbatim extracted text; use rewritten copy only.")
    lines.append("CTA: generic action reminder.")
    return "\n".join(lines)


def build_script_prompt(tokens: dict[str, Any]) -> str:
    editing = tokens.get("editing", {})
    duration = float(tokens.get("duration_sec", 0) or 0)
    beats = tokens.get("structure", {}).get("beats", [])

    lines = [
        "Script plan (non-copyright, non-identifying)",
        f"Total length: {duration:.1f}s",
        f"Cut rhythm: {float(editing.get('cuts_per_10s', 0) or 0):.1f} per 10 seconds",
        "",
    ]

    for beat in beats:
        start, end = beat.get("t", [0, 0])
        label = beat.get("label", "")
        opener = random.choice(SCRIPT_OPENERS)
        lines.append(f"[{start:.1f}-{end:.1f}] {label}: {opener}")

    lines.append("\nCTA: apply a short closing challenge with one clear ask.")
    return "\n".join(lines)


def build_generic_model_prompt(tokens: dict[str, Any], model_name: str) -> str:
    model_name = (model_name or "custom-model").strip()
    duration = float(tokens.get("duration_sec", 0) or 0)
    editing = tokens.get("editing", {})
    beats = tokens.get("structure", {}).get("beats", [])
    visual = tokens.get("visual", {})
    subtitle = tokens.get("subtitle", {})
    audio = tokens.get("audio", {})

    lines = [
        f"Generic prompt for model: {model_name}",
        f"Duration: {duration:.1f}s",
        f"Visual complexity: {visual.get('background_complexity', 'mid')} / faces: {visual.get('face_presence_ratio_est', 0):.2f}",
        "",
    ]

    lines.append("Section rules:")
    lines.extend([f"- {random.choice(GENERIC_SECTIONS)}" for _ in range(2)])
    lines.append("")

    lines.append("Beat sequence:")
    for beat in beats:
        start, end = beat.get("t", [0, 0])
        lines.append(f"- [{start:.1f} - {end:.1f}] {beat.get('label', 'BLOCK')}: keep concise and transition-focused")

    lines.append("")
    lines.append(f"Text treatment: density {subtitle.get('density', 'mid')}, avoid exact OCR strings.")
    lines.append(f"Edit rhythm: {float(editing.get('cuts_per_10s', 0) or 0):.1f} cuts per 10s, estimated BPM {audio.get('bpm_est', 0)}")
    lines.append(random.choice(GENERIC_CTA_HINTS))
    return "\n".join(lines)


def build_prompts(tokens: dict[str, Any], target: str) -> Dict[str, str]:
    if target == "sora":
        return {"sora": build_sora_prompt(tokens)}
    if target == "seedance":
        return {"seedance": build_seedance_prompt(tokens)}
    if target == "script":
        return {"script": build_script_prompt(tokens)}
    if target == "all":
        return {
            "sora": build_sora_prompt(tokens),
            "seedance": build_seedance_prompt(tokens),
            "script": build_script_prompt(tokens),
        }

    return {target: build_generic_model_prompt(tokens, target)}
