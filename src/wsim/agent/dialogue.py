"""玩家对话的确定性回应生成。

Mock 回应必须消费人设卡：挑选卡里的台词原文，并引用「遇事反应倾向」/「说话风格」。
本模块不读墙钟、不用全局随机；同一个 (npc, message, tick) 永远得到同一句话。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

PLAYER_ID = "player:visitor"
PLAYER_NAME = "访客"

# 只做轻量情感判定：够用、可解释、可回放；不引入模型。
POSITIVE_WORDS = ("谢谢", "多谢", "劳烦", "帮忙", "请", "辛苦", "好看", "厉害", "佩服", "夸")
NEGATIVE_WORDS = ("滚", "骗", "笨", "蠢", "废物", "骂", "偷", "赖账", "滚开")


def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = ""
    buf: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        m = re.match(r"\s*##\s+(.+)\s*$", line)
        if m:
            if current:
                out[current] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
            continue
        if current:
            buf.append(line)
    if current:
        out[current] = "\n".join(buf).strip()
    return out


def _clause(text: str, limit: int = 120) -> str:
    text = " ".join(x.strip() for x in text.splitlines() if x.strip())
    return text[:limit]


def extract_persona_voice(text: str) -> dict[str, Any]:
    """从人设卡提取台词、说话风格与遇事反应倾向。"""
    quotes: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*>\s*(.+)$", line)
        if not m:
            continue
        value = m.group(1).strip().strip("“”\"'")
        if value:
            quotes.append(value)
    sections = _sections(text)
    style = ""
    reaction = ""
    for title, body in sections.items():
        if "说话风格" in title:
            style = _clause(body)
        elif "遇事反应倾向" in title:
            reaction = _clause(body)
    return {"quotes": quotes, "style": style, "reaction": reaction}


def _sentiment(message: str, digest: str) -> tuple[float, float]:
    """返回 (affection_delta, trust_delta)；幅度 1 或 2。"""
    magnitude = 2.0 if int(digest[:2], 16) % 2 == 0 else 1.0
    pos = any(w in message for w in POSITIVE_WORDS)
    neg = any(w in message for w in NEGATIVE_WORDS)
    if pos and not neg:
        return magnitude, magnitude * 0.5
    if neg and not pos:
        return -magnitude, -magnitude * 0.5
    sign = 1.0 if int(digest[2:4], 16) % 2 == 0 else -1.0
    return sign * 1.0, sign * 0.5


def compose_talk(
    persona_text: str,
    npc_id: str,
    npc_name: str,
    npc_role: str,
    message: str,
    tick: int,
) -> dict[str, Any]:
    """生成回应与情感增量；上限不在这里做，由 TalkAction 统一 clamp。"""
    message = (message or "").strip()
    voice = extract_persona_voice(persona_text)
    digest = hashlib.sha256(f"{npc_id}|{message}|{tick}".encode("utf-8")).hexdigest()

    quote = ""
    if voice["quotes"]:
        quote = voice["quotes"][int(digest[:8], 16) % len(voice["quotes"])]

    reaction = voice["reaction"] or voice["style"]
    lead = f"{npc_name}（{npc_role}）听了你的话，" if npc_role else f"{npc_name}听了你的话，"
    reaction = reaction.rstrip("。；;，, ")
    body = f"{lead}{reaction}。" if reaction else f"{lead}先没有立刻作答。"
    reply = f"{body}\n「{quote}」" if quote else body
    aff, trust = _sentiment(message, digest)
    return {
        "reply": reply,
        "quote": quote,
        "style": voice["style"],
        "reaction": reaction,
        "affection_delta": aff,
        "trust_delta": trust,
    }
