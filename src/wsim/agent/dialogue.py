"""玩家对话的确定性回应生成。

模板回应必须：第一人称、20–60 字、直接回应玩家输入里的具体内容（人物/地点/
物品/动作），并把人物身份与性格融进一句话，而不是倾倒人格档案。本模块不读
墙钟、不用全局随机；同一个 (npc, message, tick, turn) 永远得到同一句话。
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

# 人设卡里的居住地是英文 id，模板里换成可朗读的地点名。
LOCATION_NAMES = {
    "market": "集市",
    "smithy": "铁匠铺",
    "inn": "客栈",
    "escort_agency": "镖局",
    "pharmacy": "药铺",
    "academy": "书院",
    "ferry": "河渡",
    "temple": "城隍庙",
}

# 意图关键词与权重：按分值取最高者，同分按 INTENT_RULES 的先后顺序。
INTENT_RULES: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] = (
    ("rejection", (("不买", 5), ("不要", 4), ("不用", 4), ("算了", 4),
                   ("免了", 4), ("没兴趣", 5), ("滚", 5), ("走开", 4), ("别烦", 4))),
    ("greeting", (("你好", 4), ("您好", 4), ("今日可好", 5), ("近来可好", 5),
                  ("气色", 4), ("幸会", 3), ("久仰", 3))),
    ("directions", (("哪里", 3), ("哪儿", 3), ("怎么走", 4), ("怎么去", 4),
                    ("落脚", 5), ("住处", 4), ("客栈", 3), ("迷路", 3))),
    ("help", (("帮我", 4), ("帮忙", 4), ("劳烦", 4), ("求你", 4), ("能不能", 3),
              ("赏脸", 5), ("请你", 4), ("建议", 3), ("怎么办", 2), ("学门手艺", 5))),
    ("safety", (("安全", 4), ("怪声", 4), ("夜里", 3), ("天黑", 3), ("巡街", 2))),
    ("object", (("这把刀", 5), ("把刀", 4), ("砍刀", 4), ("年头", 4))),
    ("speculate", (("以后", 4), ("将来", 4), ("会变", 5), ("会成", 4), ("往后", 3))),
    ("who", (("谁最", 5), ("谁有钱", 5), ("哪家", 4))),
    ("person", (("为人", 4), ("认识", 4), ("熟吗", 4), ("靠得住", 4), ("公道", 4),
                ("什么人", 3), ("师爷", 3), ("捕快", 3), ("掌柜", 3), ("九娘", 3),
                ("三娘", 3), ("读书人", 4))),
    ("news", (("听说", 3), ("新鲜事", 5), ("消息", 4), ("传闻", 4), ("外乡人", 5),
              ("招人", 4), ("官府", 2), ("加税", 2), ("最近", 2), ("镇上", 2))),
    ("business", (("肉价", 5), ("价钱", 4), ("价格", 4), ("多少钱", 4), ("几文", 4),
                  ("本钱", 5), ("盘铺子", 5), ("赊账", 5), ("下水", 4), ("瘦肉", 4),
                  ("绣品", 4), ("卖", 3), ("买", 3), ("生意", 3), ("铺子", 3),
                  ("贵", 3), ("斤", 2))),
    ("opinion", (("怕不怕", 5), ("信命", 5))),
    ("chitchat", (("平时", 4), ("除了", 3), ("家里", 4), ("还做什么", 4),
                  ("怎么切", 3), ("刀工", 4), ("跟谁学", 4))),
)

# 同一意图的两种句式，按 digest 选一种，避免机械重复。
TEMPLATES: dict[str, tuple[str, ...]] = {
    "greeting": (
        "我在呢，{role}这行我干惯了，从早忙到晚。你有话直说，我不爱绕弯子。",
        "来啦，我正守着摊子。做{role}的没别的本事，你有事尽管开口。",
    ),
    "directions": (
        "找落脚处往客栈去，我收摊后常在那儿喝两碗，提我名字好使。",
        "{place}这带我熟，往客栈走能吃能睡；迷了路就来摊上问我。",
    ),
    "news": (
        "「{topic}」这事我也听了些，{place}这两天风声紧，我只顾守好摊子。",
        "你问「{topic}」，街面上传得热闹；我不掺和，做{role}的只管本分。",
    ),
    "person": (
        "「{topic}」我打过照面，不算深交。干{role}这些年，我看人只信日久见人心。",
        "「{topic}」啊，面上热络，我瞧这人心里都有算盘。你跟这人来往，多留个心眼。",
    ),
    "who": (
        "「{topic}」这话不好明说，财不露白。你去钱庄那头打听，比问我强。",
        "「{topic}」我可不敢乱指名，钱的事传出去要惹祸，你自己瞧人去吧。",
    ),
    "business": (
        "「{topic}」好说，今日行情见涨；你诚心要，价钱我让一文，斤两不短。",
        "「{topic}」我不含糊，先看货再论价；熟客抹零，生客照规矩来。",
    ),
    "chitchat": (
        "「{topic}」嘛，我做{role}的日子就是天亮忙到天黑，落几个钱心里才踏实。",
        "「{topic}」我懂的不多，只晓得本分行当；旁的闲话，我不往外传。",
    ),
    "help": (
        "「{topic}」这忙我能搭把手，丑话说前头，账目得慢慢算清。",
        "「{topic}」你先讲难处；能应的我拍胸脯，不能应的也不诓你。",
    ),
    "rejection": (
        "不买也不打紧，买卖不成情义在，下回你来，我照样给你留好的。",
        "行，你不乐意就作罢，我不强留；做{role}这行，靠的是回头客。",
    ),
    "safety": (
        "「{topic}」你问着了，天黑我劝你少往僻静处走，钱袋揣紧些。",
        "「{topic}」我也留神，夜里收了摊就回，你别一个人走暗路。",
    ),
    "object": (
        "「{topic}」跟了我些年头，用顺手了；谁要动它，我可不依。",
        "「{topic}」是吃饭的家伙，我天天擦天天磨，舍不得换。",
    ),
    "speculate": (
        "「{topic}」谁说得准，我只顾眼下把手上的活做好，日子总归要过。",
        "往后的事我不多想，天亮了照旧出摊，能把今天的钱挣到手就成。",
    ),
    "opinion": (
        "「{topic}」啊，怕也没用，真来了我照旧出摊，先把今天的钱挣了。",
        "「{topic}」这事我想得开，日子还得过，谁也不比谁少熬一天。",
    ),
    "deflection": (
        "这话我没听出个头绪，你再说明白些，我才好接。",
        "你说的我一时没摸着门道，换个说法，我再答你。",
    ),
}

# 各意图里更贴题的人设台词优先；没有命中时按 digest 轮换。
QUOTE_HINTS: dict[str, tuple[str, ...]] = {
    "business": ("账", "钱", "价", "斤两", "卖", "买", "货"),
    "rejection": ("账", "认", "糊弄", "兄弟"),
    "safety": ("刀", "犯法", "盯着"),
    "object": ("刀", "斤两"),
    "help": ("包在", "话"),
}

# 具体话题优先于从原句里切出来的短语。
TOPIC_HINTS: tuple[tuple[str, str], ...] = (
    ("肉价", "肉价"), ("价钱", "价钱"), ("价格", "价钱"), ("多少钱", "价钱"),
    ("本钱", "本钱"), ("盘铺子", "铺子"), ("赊账", "赊账"), ("猪下水", "下水"),
    ("下水", "下水"), ("瘦肉", "瘦肉"), ("绣品", "绣品"), ("新鲜事", "新鲜事"),
    ("外乡人", "外乡人"), ("招人", "招人入伙"), ("北边", "北边"), ("怪声", "怪声"),
    ("加税", "加税"), ("官府", "官府"), ("信命", "命"), ("学门手艺", "学手艺"),
    ("喝碗酒", "喝酒"), ("喝酒", "喝酒"), ("卖多少肉", "行情"),
    ("家里", "家里的事"), ("平时", "平日营生"),
    ("除了", "平日营生"), ("刀工", "刀工"), ("这把刀", "这把刀"),
    ("把刀", "这把刀"), ("客栈", "落脚"),
)

TOPIC_DEFAULT = {
    "greeting": "这话",
    "directions": "落脚",
    "news": "新鲜事",
    "person": "这人",
    "who": "谁最有钱",
    "business": "这买卖",
    "chitchat": "这话",
    "help": "这事",
    "rejection": "买卖",
    "safety": "夜里的事",
    "object": "这把刀",
    "speculate": "往后的日子",
    "opinion": "这事",
    "deflection": "这话",
}

_LEAD_WORDS = (
    "最后一个问题", "听说", "最近", "近来", "今日", "今天", "这镇上", "镇上",
    "你觉得", "你说", "我想问", "我想", "请问", "那么", "还有", "若是", "如果",
    "若有人", "若", "我初来乍到",
)

_TAIL_PATTERNS = (
    "为何", "为什么", "怎么", "如何", "怎么样", "多少", "几文", "多少钱",
    "可有什么", "有什么", "有没有", "知道吗", "认识吗", "熟吗", "靠得住吗",
    "公道吗", "肯赏脸吗", "怎么办", "怎么切最好", "怎么切", "是跟谁学的",
    "跟谁学的", "好不好", "行不行", "有些年头了", "看着有些年头了",
    "为人如何", "为人怎么样",
)


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


def _quote_usable(quote: str) -> bool:
    """过滤掉会出戏的台词：Markdown 标记、第三人称叙述。"""
    return not re.search(r"[他她]|\*\*|__|`|\n", quote)


def extract_persona_voice(text: str) -> dict[str, Any]:
    """从人设卡提取台词、说话风格与遇事反应倾向。"""
    quotes: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*>\s*(.+)$", line)
        if not m:
            continue
        value = m.group(1).strip().strip("“”\"'")
        if value and _quote_usable(value):
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


def extract_persona_profile(text: str, npc_role: str = "") -> dict[str, str]:
    """只取模板需要的两件事：身份称谓与常驻地点，不读取整段档案。"""
    fields: dict[str, str] = {}
    for m in re.finditer(r"^-\s*([^：:]+)[：:]\s*(.+)$", text, re.M):
        fields.setdefault(m.group(1).strip(), m.group(2).strip())
    role = npc_role.strip()
    if not role:
        role = re.split(r"[，,]", fields.get("身份", ""))[0].strip()
    place = LOCATION_NAMES.get(fields.get("居住地", "").strip(), "")
    if not place:
        for name in LOCATION_NAMES.values():
            if name in fields.get("常出没", ""):
                place = name
                break
    return {"role": role or "手艺人", "place": place or "集市"}


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


def _intent(message: str) -> str:
    # 开头的“三娘，”“段大哥，”只是呼语，不参与意图打分。
    message = re.sub(r"^[^，,]{1,4}[，,]\s*", "", message or "")
    best = "deflection"
    best_score = 0
    for name, rules in INTENT_RULES:
        score = sum(weight for word, weight in rules if word in message)
        if score > best_score:
            best, best_score = name, score
    return best


def _person_subject(message: str) -> str:
    """从问句里取具体人名，取不到再退回“读书人”这类身份词。"""
    m = re.search(r"([\u4e00-\u9fa5]{1}(?:掌柜|师爷|捕快|九娘|三娘))", message)
    if m:
        return re.sub(r"^(?:和|与|跟|同)", "", m.group(1))
    for word in ("读书人", "书生", "货郎", "掌柜", "师爷", "捕快"):
        if word in message:
            return word
    return ""


def _topic(message: str, intent: str) -> str:
    if intent == "speculate":
        return "往后的日子"
    if intent == "safety":
        return "怪声" if "怪声" in message else "夜里的事"
    if intent == "person":
        name = _person_subject(message)
        if name:
            return name
    if intent == "directions":
        return "落脚"
    text = (message or "").strip()
    text = re.sub(r"^[^，,]{1,4}[，,]\s*", "", text)  # 去掉“段大哥，”“三娘，”这类呼语
    for word in _LEAD_WORDS:
        if text.startswith(word):
            text = text[len(word):]
            break
    text = re.split(r"[，,。；;！!]", text)[0]
    text = re.sub(r"^(?:你们|我们|他们|你这|我那|他那|这镇子|镇子|镇上|这|那|你|我|他|她)", "", text)
    text = text.strip("　 ，,。；;！!？?…“”\"'")
    for tail in _TAIL_PATTERNS:
        if text.endswith(tail):
            text = text[: -len(tail)]
            break
    text = text.strip("　 ，,。；;！!？?…“”\"'")
    if text.startswith("和"):
        text = text[1:]
    text = re.sub(r"^(?:的)+|(?:的)+$", "", text)
    for raw, label in TOPIC_HINTS:
        if raw in message:
            return label
    if 0 < len(text) <= 10:
        return text
    return TOPIC_DEFAULT.get(intent, "这话")


def _pick_quote(voice: dict[str, Any], intent: str, digest: str, last_reply: str) -> str:
    quotes = list(voice["quotes"])
    if not quotes:
        return ""
    hints = QUOTE_HINTS.get(intent, ())
    base = int(digest[:8], 16) % len(quotes)
    ordered = sorted(
        range(len(quotes)),
        key=lambda i: (
            -sum(1 for h in hints if h in quotes[i]),
            len(quotes[i]),
            (i - base) % len(quotes),
        ),
    )
    for i in ordered:
        quote = quotes[i]
        if len(quotes) > 1 and quote in (last_reply or ""):
            continue
        return quote
    return quotes[ordered[0]]


def _quote_fits(intent: str, quote: str) -> bool:
    """只有台词与当前意图沾边时才收尾；打招呼和回避必须有台词。"""
    if not quote:
        return False
    if intent in ("greeting", "deflection"):
        return True
    return any(h in quote for h in QUOTE_HINTS.get(intent, ()))


def _trim(body: str, quote: str) -> str:
    """优先保留台词，同时把长度压进 20–60 字。"""
    if len(body) + len(quote) <= 60:
        return body + quote
    if len(body) <= 60:
        return body
    cut = body[:60]
    for mark in ("。", "；", "，", "！"):
        pos = cut.rfind(mark)
        if pos >= 19:
            return cut[: pos + 1]
    return cut


def compose_talk(
    persona_text: str,
    npc_id: str,
    npc_name: str,
    npc_role: str,
    message: str,
    tick: int,
    turn: int = 0,
    last_message: str = "",
    last_reply: str = "",
) -> dict[str, Any]:
    """生成回应与情感增量；上限不在这里做，由 TalkAction 统一 clamp。

    回应是确定性模板：先判意图，再取玩家输入里的具体话题，用第一人称接一句，
    最后按意图挑一条人设台词收尾。`npc_name` 只用于摘要，不写进对白，避免
    “某人听了你的话”这类第三人称旁白。
    """
    message = (message or "").strip()
    voice = extract_persona_voice(persona_text)
    profile = extract_persona_profile(persona_text, npc_role)
    digest = hashlib.sha256(f"{npc_id}|{message}|{tick}|{turn}".encode("utf-8")).hexdigest()
    sent_digest = hashlib.sha256(f"{npc_id}|{message}|{tick}".encode("utf-8")).hexdigest()

    intent = _intent(message)
    topic = _topic(message, intent)
    variants = TEMPLATES[intent]
    body = variants[int(digest, 16) % len(variants)].format(
        role=profile["role"], place=profile["place"], topic=topic
    )
    quote = _pick_quote(voice, intent, digest, last_reply)
    if not _quote_fits(intent, quote):
        quote = ""
    reply = _trim(body, quote)
    aff, trust = _sentiment(message, sent_digest)
    return {
        "reply": reply,
        "quote": quote,
        "intent": intent,
        "topic": topic,
        "style": voice["style"],
        "reaction": voice["reaction"],
        "affection_delta": aff,
        "trust_delta": trust,
    }
