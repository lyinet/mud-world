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

# 意图优先按语义类别分化：不同类别使用不同句式骨架，禁止只替换话题名词。
INTENT_RULES: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] = (
    ("rejection", (("不买", 5), ("不要", 4), ("不用", 4), ("算了", 4),
                   ("免了", 4), ("没兴趣", 5), ("滚", 5), ("走开", 4), ("别烦", 4))),
    ("greeting", (("你好", 4), ("您好", 4), ("今日可好", 5), ("近来可好", 5),
                  ("气色", 4), ("幸会", 3), ("久仰", 3))),
    ("directions", (("哪里", 3), ("哪儿", 3), ("怎么走", 4), ("怎么去", 4),
                    ("落脚", 5), ("住处", 4), ("客栈", 3), ("迷路", 3))),
    ("help", (("帮我", 4), ("帮忙", 4), ("劳烦", 4), ("求你", 4), ("能不能", 3),
              ("赏脸", 5), ("请你", 4), ("建议", 3), ("借", 2), ("学门手艺", 5))),
    ("credit", (("赊账", 6), ("赊", 4), ("借钱", 6), ("借我", 4), ("欠账", 5),
                ("欠", 3), ("账期", 5), ("还钱", 5), ("缓两天", 4), ("手头紧", 3))),
    ("gossip", (("听说", 3), ("新鲜事", 5), ("消息", 4), ("传闻", 4), ("外乡人", 5),
                ("招人", 4), ("官府", 2), ("加税", 2), ("最近", 2), ("镇上", 2),
                ("为人", 4), ("认识", 4), ("熟吗", 4), ("靠得住", 4), ("公道", 4),
                ("什么人", 3), ("谁最", 5), ("谁有钱", 5), ("哪家", 4),
                ("师爷", 3), ("捕快", 3), ("掌柜", 3), ("九娘", 3), ("三娘", 3),
                ("读书人", 4))),
    ("price", (("肉价", 5), ("价钱", 4), ("价格", 4), ("多少钱", 4), ("几文", 4),
               ("本钱", 5), ("盘铺子", 5), ("下水", 4), ("瘦肉", 4), ("绣品", 4),
               ("卖", 3), ("买", 3), ("生意", 3), ("铺子", 3), ("贵", 3), ("斤", 2))),
    ("chitchat", (("平时", 4), ("除了", 3), ("家里", 4), ("还做什么", 4),
                  ("怎么切", 3), ("刀工", 4), ("跟谁学", 4), ("怕不怕", 5),
                  ("信命", 5), ("安全", 4), ("怪声", 4), ("夜里", 3), ("天黑", 3),
                  ("巡街", 2), ("这把刀", 5), ("把刀", 4), ("砍刀", 4), ("年头", 4),
                  ("以后", 4), ("将来", 4), ("会变", 5), ("会成", 4), ("往后", 3))),
)

# 每个骨架只表达一件事；跨类别不复用同一句尾口号或同一组语义内容。
TEMPLATES: dict[str, tuple[str, ...]] = {
    "greeting.a": ("我在呢，{role}这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。",),
    "greeting.b": ("来啦，我正守着摊子；做{role}的没别的本事，你有事尽管开口。",),
    "directions.a": ("找落脚处往东街走，过了石桥见红灯笼就是客栈；我摊子在{place}，走岔了回来问我。",),
    "directions.b": ("从{place}往北两条街，门挂蓝布帘的是客栈；路口老槐树作记号，走岔了回来问我。",),
    "gossip.person.a": ("「{topic}」这人我打过照面：嘴上是热络的，心里各有一本账；你跟这人来往，先护住钱袋。",),
    "gossip.person.b": ("「{topic}」听来的话分两说：有人夸这人仗义，有人说这人精算；我只信亲眼见的。",),
    "gossip.event.a": ("「{topic}」街面上传得凶，一版比一版玄；我只看亲眼见的，你别再往外传。",),
    "gossip.event.b": ("「{topic}」这事我听到两耳朵，真真假假掺着；你要沾边，先把日子过稳。",),
    "gossip.who": ("「{topic}」财不露白，我不敢乱指名；你去钱庄门口站半日，看谁进出不打怵。",),
    "price.a": ("「{topic}」按今日行情走，一斤上下浮动两文；你诚心要，零头我抹，秤上不少你。",),
    "price.b": ("「{topic}」我这儿看货定价，好的贵两文，次的折一文；称完你验秤，短一两我赔。",),
    "credit.a": ("「{topic}」可以赊，三天为限；第四天我上门讨，别让我空手回。",),
    "credit.b": ("「{topic}」我把话说明白：现钱付一半，余下三日清；到期不还，下回别再开口。",),
    "chitchat.daily.a": ("「{topic}」我天亮磨刀、晌午出摊，收摊爱喝两口；日子磨人，手上茧子比话实在。",),
    "chitchat.daily.b": ("「{topic}」我这人不讲究，案板裂了就拿铁钉楔上；闲下来只喝酒，不赌也不凑热闹。",),
    "chitchat.craft.a": ("「{topic}」这刀我每日都磨，钝了就换；手艺是挨骂挨出来的，如今靠它糊口。",),
    "chitchat.craft.b": ("「{topic}」跟师傅学了三年才出师，刀口深浅全靠手感；你看着简单，我练了半辈子。",),
    "chitchat.opinion.a": ("「{topic}」我不敢说看透，只知道天亮出摊、天黑收工；怕也挡不住事，先把今天过完。",),
    "chitchat.opinion.b": ("「{topic}」要问我怕不怕，我说怕也没用；该来的挡不住，先把今天的钱挣了。",),
    "chitchat.fate.a": ("「{topic}」命这事我不常想；该挣的钱照挣，该吃的苦照吃，怨天没用。",),
    "chitchat.fate.b": ("「{topic}」信不信命都得干活；我把今天过踏实，明天怎样交给天。",),
    "chitchat.safety.a": ("「{topic}」我夜里收摊先磨刀，再挑灯底下走；僻静巷子不踩，钱袋塞怀里。",),
    "chitchat.safety.b": ("「{topic}」夜里我不走暗巷，听见怪响就绕道；你要出门最好结伴，别省那两步路。",),
    "chitchat.future.a": ("「{topic}」往后我不敢乱猜，摊子撑一天算一天；攒下钱先清欠账，别的以后再说。",),
    "chitchat.future.b": ("「{topic}」这镇子往后怎样，我说不准；我只管把今天的肉卖完，明天的事明天愁。",),
    "help.a": ("「{topic}」我搭把手可以，先讲清要办到哪一步；能应的我应，办不到我当面回你。",),
    "help.b": ("「{topic}」这忙我帮，但有条件：不沾赌、不赊账；你应得下，我明早就去。",),
    "help.invite": ("「{topic}」我应了，地方你挑；酒钱各付各的，别灌我，喝多了我还得看摊。",),
    "rejection.a": ("不买就不买，我不恼；摊子摆到天黑，你改主意再回来。",),
    "rejection.b": ("你不乐意就算了，强卖没意思；我记你一张脸，下回价照旧。",),
    "deflection.a": ("这话我没听出个头绪，你再说明白些，我才好接。",),
    "deflection.b": ("你说的我一时没摸着门道，换个说法，我再答你。",),
}

# 只有“回避”骨架保留人设台词；避免同一句口号跨类别复用。
QUOTE_HINTS: dict[str, tuple[str, ...]] = {}

# 具体话题优先于从原句里切出来的短语。
TOPIC_HINTS: tuple[tuple[str, str], ...] = (
    ("肉价", "肉价"), ("价钱", "价钱"), ("价格", "价钱"), ("多少钱", "价钱"),
    ("本钱", "本钱"), ("盘铺子", "铺子"), ("赊账", "赊账"), ("借钱", "借钱"),
    ("账期", "账期"), ("欠账", "欠账"), ("缓两天", "账期"), ("手头紧", "账期"),
    ("猪下水", "下水"), ("下水", "下水"), ("瘦肉", "瘦肉"), ("绣品", "绣品"),
    ("新鲜事", "新鲜事"), ("外乡人", "外乡人"), ("招人", "招人入伙"),
    ("北边", "北边"), ("怪声", "怪声"), ("加税", "加税"), ("官府", "官府"),
    ("信命", "命"), ("学门手艺", "学手艺"), ("喝碗酒", "喝酒"), ("喝酒", "喝酒"),
    ("卖多少肉", "行情"), ("卖多少", "行情"), ("铺子", "铺子"),
    ("家里", "家里的事"), ("平时", "平日营生"),
    ("除了", "平日营生"), ("刀工", "刀工"), ("这把刀", "这把刀"),
    ("把刀", "这把刀"), ("客栈", "落脚"),
)

TOPIC_DEFAULT = {
    "greeting": "这话",
    "directions": "落脚",
    "gossip": "新鲜事",
    "price": "这买卖",
    "credit": "赊账",
    "chitchat": "这话",
    "help": "这忙",
    "rejection": "买卖",
    "deflection": "这话",
}

_LEAD_WORDS = (
    "最后一个问题", "听说", "最近", "近来", "今日", "今天", "这镇上", "镇上",
    "你觉得", "你说", "我想问", "我想", "请问", "那么", "还有", "若是", "如果",
    "若有人", "若", "我初来乍到", "能帮我", "帮我", "劳烦", "能不能",
)

# 只去掉真正的呼语（“段大哥，”“三娘，”），不要把“借钱给你，……”这类内容截掉。
_VOCATIVE_RE = re.compile(
    r"^(?:[\u4e00-\u9fa5]{0,2}(?:大哥|大姐|掌柜|师爷|捕快|先生|娘子|姑娘|老板|三娘|九娘))[，,]"
)

_TAIL_PATTERNS = (
    "为何", "为什么", "怎么", "如何", "怎么样", "多少", "几文", "多少钱",
    "可有什么", "有什么", "有没有", "知道吗", "认识吗", "熟吗", "靠得住吗",
    "公道吗", "肯赏脸吗", "怎么办", "怎么切最好", "怎么切", "是跟谁学的",
    "跟谁学的", "好不好", "行不行", "有些年头了", "看着有些年头了",
    "为人如何", "为人怎么样", "吗", "呢", "吧",
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
    message = _VOCATIVE_RE.sub("", message or "")
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
    if intent == "directions":
        return "落脚"
    if intent == "credit":
        for word in ("赊账", "借钱", "账期", "欠账", "手头紧", "缓两天"):
            if word in message:
                return "账期" if word in ("手头紧", "缓两天") else word
        return "赊账"
    if intent == "gossip":
        if any(w in message for w in ("谁最", "谁有钱", "哪家")):
            return "谁最有钱"
        name = _person_subject(message)
        if name:
            return name
    if intent == "chitchat":
        if any(w in message for w in ("安全", "怪声", "夜里", "天黑", "巡街")):
            return "怪声" if "怪声" in message else "夜里的事"
        if any(w in message for w in ("以后", "将来", "会变", "会成", "往后")):
            return "往后的日子"
        if any(w in message for w in ("怕不怕", "信命")):
            return "加税" if "加税" in message else "命"
        if any(w in message for w in ("刀工", "跟谁学")):
            return "刀工"
        if any(w in message for w in ("这把刀", "把刀", "砍刀", "年头")):
            return "这把刀"
        if "家里" in message:
            return "家里的事"
        if any(w in message for w in ("平时", "除了", "还做什么")):
            return "平日营生"
    if intent == "help":
        if any(w in message for w in ("喝酒", "赏脸", "请你", "喝碗酒")):
            return "喝酒"
        if any(w in message for w in ("学门手艺", "建议")):
            return "学手艺"
    text = (message or "").strip()
    text = _VOCATIVE_RE.sub("", text)
    for _ in range(2):
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


def _skeleton(intent: str, message: str, turn: int) -> str:
    """同一意图内再用子类骨架分化；不同意图不复用同一骨架 id。

    变体按 turn 交替，保证同一 NPC 连续两轮不会给出完全相同的句子。
    """

    def ab(base: str) -> str:
        return f"{base}.{'ab'[turn % 2]}"

    if intent == "gossip":
        if any(w in message for w in ("谁最", "谁有钱", "哪家")):
            return "gossip.who"
        if _person_subject(message) or any(
            w in message for w in ("为人", "认识", "熟吗", "靠得住", "公道")
        ):
            return ab("gossip.person")
        return ab("gossip.event")
    if intent == "chitchat":
        if any(w in message for w in ("安全", "怪声", "夜里", "天黑", "巡街")):
            return ab("chitchat.safety")
        if any(w in message for w in ("以后", "将来", "会变", "会成", "往后")):
            return ab("chitchat.future")
        if any(w in message for w in ("怕不怕", "信命")):
            if "信命" in message:
                return ab("chitchat.fate")
            return ab("chitchat.opinion")
        if any(w in message for w in ("刀工", "跟谁学", "这把刀", "把刀", "砍刀", "年头")):
            return ab("chitchat.craft")
        return ab("chitchat.daily")
    if intent == "help" and any(
        w in message for w in ("喝酒", "赏脸", "请你", "喝碗酒")
    ):
        return "help.invite"
    return ab(intent)


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
    skeleton = _skeleton(intent, message, turn)
    body = TEMPLATES[skeleton][0].format(
        role=profile["role"], place=profile["place"], topic=topic
    )
    # 台词只挂在“回避”骨架上，避免不同意图复用同一句尾口号。
    quote = _pick_quote(voice, intent, digest, last_reply) if intent == "deflection" else ""
    reply = _trim(body, quote)
    aff, trust = _sentiment(message, sent_digest)
    return {
        "reply": reply,
        "quote": quote,
        "intent": intent,
        "skeleton": skeleton,
        "topic": topic,
        "style": voice["style"],
        "reaction": voice["reaction"],
        "affection_delta": aff,
        "trust_delta": trust,
    }
