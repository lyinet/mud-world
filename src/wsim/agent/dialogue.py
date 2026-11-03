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
    "gossip.person.reputation.a": ("{topic}这人我打过照面：嘴上是热络的，心里各有一本账；你跟这人来往，先护住钱袋。",),
    "gossip.person.reputation.b": ("{topic}我听过两说：有人夸这人仗义，有人说这人精算；我只信亲眼见的。",),
    "gossip.person.justice.a": ("{topic}办案我见过两回：嘴上讲公道，落笔看人情；你要递状子，先把证人备齐。",),
    "gossip.person.justice.b": ("{topic}断事不算糊涂，也不全按律条；我劝你有冤就去衙门，别只信旁人嚼舌。",),
    "gossip.person.relation.acquaintance.a": ("{topic}我打过几回照面，点头的交情；要托这人办事，先把丑话讲在前头。",),
    "gossip.person.relation.acquaintance.b": ("{topic}算认识，没深交；托人办事前，我先看这人办不办得成。",),
    "gossip.person.relation.closeness.a": ("{topic}跟我算不上熟，见面能说两句；这人底细我不清楚，你别把要紧事押上去。",),
    "gossip.person.relation.closeness.b": ("{topic}交情不深，喝过一回酒；真要共事，我还得再看看。",),
    "gossip.type.a": ("{topic}这号人我说不准：有老实念书的，也有拿字纸算人心的；你多留个心眼。",),
    "gossip.type.b": ("{topic}哪一类人都有好坏；我不看身份看做事，你相处久了自然知道。",),
    "gossip.impact.a": ("{topic}那边一乱，贩猪的车就少；肉价往上走，我买卖反倒多两单，心里并不踏实。",),
    "gossip.impact.b": ("{topic}那边不太平，路上查得紧；进货慢半拍，我只能抬价，赚的是辛苦钱。",),
    "gossip.passersby.a": ("外乡人前日过去两拨，都急着赶路；有一个打听客栈，我指了东街。",),
    "gossip.passersby.b": ("过路的外乡人不少，问价的多、落脚的少；我看这些人身上没多少盘缠。",),
    "gossip.recruit.a": ("招人入伙的话我听了一耳朵，说是镖局缺人手；工钱不高，你先别急着应。",),
    "gossip.recruit.b": ("我跟你说，是有人在招人，走镖的活计要签生死状；你家里没牵挂才去得。",),
    "gossip.event.a": ("街面上传得凶，一版比一版玄；我只看亲眼见的，你别再往外传。",),
    "gossip.event.b": ("这事我听到两耳朵，真真假假掺着；你要沾边，先把日子过稳。",),
    "gossip.who": ("财不露白，我不敢乱指名；你去钱庄门口站半日，看谁进出不打怵。",),
    "price.meat.a": ("肉价这条，我按今日行情走，一斤上下浮动两文；你诚心要，零头我抹，秤上不少你。",),
    "price.meat.b": ("肉价我这儿看货定价，好的贵两文，次的折一文；称完你验秤，短一两我赔。",),
    "price.meat.reason.a": ("肉价涨是上月猪少，贩子抬了价；这几日我这儿回落两文，你要买趁早。",),
    "price.meat.reason.b": ("价贵不怪我，进价就高；等下一车猪到了，我能让一文。",),
    "price.volume.a": ("铺子一天卖半扇猪，逢集多两成；肉价随行就市，我薄利走量。",),
    "price.volume.b": ("平日半扇，赶集能走一整扇；卖多卖少看天，我价不乱喊。",),
    "price.offal.a": ("下水今早留了一副，洗得干净；你要，我按斤称，晚了就没。",),
    "price.offal.b": ("一副刚收拾出来，心肝都在；要就趁早，我隔夜不留。",),
    "price.cut.a": ("瘦肉我教你半斤逆纹切薄片，快火一炒就嫩；要炖就切块，别拿刀背砸。",),
    "price.cut.b": ("瘦肉先剔筋膜再下刀，片要薄、丝要顺；我切半斤给你看，别切太厚。",),
    "price.other.a": ("{topic}不是我的行当，我只懂肉案；你要问这路货，得找做这行的行家，别让我瞎报价。",),
    "price.other.b": ("{topic}我没经手，价我说不准；你去问正经做这行的，别听我外行话。",),
    "price.capital.a": ("盘铺子的本钱，小门脸少说要二十两，押一付三；我劝你先盘账，再谈价。",),
    "price.capital.b": ("盘个铺子得备二十两上下，装修另算；我说实话，钱不够先合伙，别硬撑。",),
    "credit.a": ("这笔可以赊，三天为限；第四天我上门讨，别让我空手回。",),
    "credit.b": ("我把话说明白：现钱付一半，余下三日清；到期不还，下回别再开口。",),
    "chitchat.daily.a": ("卖完肉我收摊喝酒，偶尔去赌摊边站站；别的营生没本钱，也不眼馋。",),
    "chitchat.daily.b": ("除了守摊，我闲下来只喝酒；赌不沾，别的活计也插不上手。",),
    "chitchat.craft.origin.a": ("刀工是跟师傅学的，三年才出师；刀口深浅全靠手感，你看着简单，我练了半辈子。",),
    "chitchat.craft.origin.b": ("我师父脾气坏，骂了三年才放我出师；如今这刀口，是挨骂挨出来的。",),
    "chitchat.craft.knife.a": ("这把刀用了七八年，钝了就磨；刀口我从不外借，砍骨全靠它。",),
    "chitchat.craft.knife.b": ("刀是老师傅打的，钢口好；年头久了握柄发滑，我缠了布条。",),
    "chitchat.opinion.a": ("我不敢说看透，只知道天亮出摊、天黑收工；怕也挡不住事，先把今天过完。",),
    "chitchat.opinion.b": ("要问我怕不怕，我说怕也没用；该来的挡不住，先把今天的钱挣了。",),
    "chitchat.fate.a": ("命这事我不常想；该挣的钱照挣，该吃的苦照吃，怨天没用。",),
    "chitchat.fate.b": ("信不信命都得干活；我把今天过踏实，明天怎样交给天。",),
    "chitchat.safety.a": ("我夜里收摊先磨刀，再挑灯底下走；僻静巷子不踩，钱袋塞怀里。",),
    "chitchat.safety.b": ("夜里我不走暗巷，听见怪响就绕道；你要出门最好结伴，别省那两步路。",),
    "chitchat.sound.a": ("后墙根响过两回，像野猫又叫春；我提刀出去看，连个影都没有。",),
    "chitchat.sound.b": ("夜里听着像磨刀，又像风刮棚布；我绕过去查过，没见人。",),
    "chitchat.family.a": ("家里就我一个光棍，一人吃饱全家不饿；亲戚走动少，倒也清净。",),
    "chitchat.family.b": ("屋里没别人，就我守着这摊；逢年过节才回去看看老娘。",),
    "chitchat.future.a": ("往后我不敢乱猜，摊子撑一天算一天；攒下钱先清欠账，别的以后再说。",),
    "chitchat.future.b": ("这镇子往后怎样，我说不准；我只管把今天的肉卖完，明天的事明天愁。",),
    "help.a": ("这忙我搭把手可以，先讲清要办到哪一步；能应的我应，办不到我当面回你。",),
    "help.b": ("这忙我帮，但有条件：不沾赌、不赊账；你应得下，我明早就去。",),
    "help.skill.a": ("学手艺我劝你找口碑好的师傅，先打三年下手；别怕挨骂，手艺是磨出来的。",),
    "help.skill.b": ("想学手艺，我跟你说，先看师傅带不带人，再谈工钱；头一年只求站稳，别急着出师。",),
    "help.invite": ("喝酒我应了，地方你挑；酒钱各付各的，别灌我，喝多了我还得看摊。",),
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


_PERSON_NAME_RE = re.compile(
    r"([\u4e00-\u9fa5]{1}(?:掌柜|师爷|捕快|九娘|三娘|老板|先生|娘子|姑娘))"
)


def _person_name(message: str) -> str:
    """只取具体人名/带姓称谓；“读书人”这类泛称不算人名。"""
    m = _PERSON_NAME_RE.search(message)
    if not m:
        return ""
    return re.sub(r"^(?:和|与|跟|同)", "", m.group(1))


def _person_generic(message: str) -> str:
    """取泛称人群/身份词，走“人物类型”骨架，不冒充具体某人。"""
    for word in ("读书人", "书生", "货郎", "文人", "手艺人"):
        if word in message:
            return word
    return ""


def _object_type(message: str, intent: str) -> str:
    """识别“诉求对象”，与意图组成二维骨架选择。

    商品细分到肉/下水/切法/其他货；人物区分具体人名与泛称；另有地点、
    钱务、消息、杂务。泛称人物必须走 gossip.type，不能混进人名骨架。
    """
    if intent == "price":
        if any(w in message for w in ("本钱", "盘铺子", "盘个", "租金", "押金")):
            return "capital"
        if any(w in message for w in ("卖多少", "一天能卖", "走量", "能走多少")):
            return "volume"
        if any(w in message for w in ("猪下水", "下水", "心肝", "肚")):
            return "offal"
        if any(
            w in message for w in ("瘦肉", "肥肉", "五花", "里脊", "怎么切", "切法", "切最")
        ):
            return "cut"
        if any(w in message for w in ("绣品", "绣活", "布匹", "药材")):
            return "other"
        if any(w in message for w in ("为何", "为什么", "涨", "跌", "贵了", "便宜了", "回落")):
            return "reason"
        return "meat"
    if intent == "gossip":
        if any(w in message for w in ("谁最", "谁有钱", "哪家")):
            return "who"
        if any(w in message for w in ("招人", "入伙", "收人", "缺人手")):
            return "recruit"
        if any(w in message for w in ("外乡人", "过路", "经过", "路人")):
            return "passersby"
        if any(w in message for w in ("不太平", "受影响", "生意", "北边")):
            return "impact"
        if _person_name(message):
            return "person"
        if _person_generic(message):
            return "type"
        return "news"
    if intent == "chitchat":
        if any(w in message for w in ("家里", "亲人", "几口人", "老娘", "婆娘")):
            return "family"
        if any(w in message for w in ("怪声", "响动", "什么声音", "动静")):
            return "sound"
        if any(w in message for w in ("安全", "夜里", "天黑", "巡街", "暗巷")):
            return "safety"
        if any(w in message for w in ("以后", "将来", "会变", "会成", "往后")):
            return "future"
        if "信命" in message or "命" in message:
            return "fate"
        if any(w in message for w in ("怕不怕", "加税")):
            return "opinion"
        if any(w in message for w in ("刀工", "跟谁学", "学出来的", "哪儿学")):
            return "craft_origin"
        if any(w in message for w in ("这把刀", "把刀", "砍刀", "年头")):
            return "craft_knife"
        return "daily"
    if intent == "help":
        if any(w in message for w in ("喝酒", "赏脸", "请你", "喝碗酒")):
            return "invite"
        if any(w in message for w in ("学门手艺", "学手艺", "拜师", "收徒", "建议")):
            return "skill"
        return "errand"
    if intent == "directions":
        return "place"
    if intent == "credit":
        return "credit"
    if intent == "rejection":
        return "refusal"
    return "none"


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
        name = _person_name(message)
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


def _skeleton(intent: str, message: str, turn: int, obj: str) -> str:
    """二维骨架选择：意图类别 + 诉求对象，再用 turn 交替 a/b 变体。

    不同对象不复用同一骨架 id，避免“所有生意问题都答肉价”；同一对象
    连续两轮由 turn 变体轮换，不会给出同一句。
    """

    def ab(base: str) -> str:
        return f"{base}.{'ab'[turn % 2]}"

    if intent == "price":
        base = {
            "capital": "price.capital",
            "volume": "price.volume",
            "offal": "price.offal",
            "cut": "price.cut",
            "other": "price.other",
            "reason": "price.meat.reason",
        }.get(obj, "price.meat")
        return ab(base)
    if intent == "gossip":
        if obj == "who":
            return "gossip.who"
        if obj in ("impact", "recruit", "passersby"):
            return ab(f"gossip.{obj}")
        if obj == "type":
            return ab("gossip.type")
        if obj == "person":
            if any(w in message for w in ("办案", "公道", "断案", "官司")):
                return ab("gossip.person.justice")
            if any(w in message for w in ("认识", "打过交道")):
                return ab("gossip.person.relation.acquaintance")
            if any(w in message for w in ("熟吗", "熟不熟", "交情")):
                return ab("gossip.person.relation.closeness")
            return ab("gossip.person.reputation")
        return ab("gossip.event")
    if intent == "chitchat":
        base = {
            "family": "chitchat.family",
            "sound": "chitchat.sound",
            "safety": "chitchat.safety",
            "future": "chitchat.future",
            "fate": "chitchat.fate",
            "opinion": "chitchat.opinion",
            "craft_origin": "chitchat.craft.origin",
            "craft_knife": "chitchat.craft.knife",
        }.get(obj, "chitchat.daily")
        return ab(base)
    if intent == "help":
        if obj == "invite":
            return "help.invite"
        if obj == "skill":
            return ab("help.skill")
        return ab("help")
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
    obj = _object_type(message, intent)
    topic = _topic(message, intent)
    skeleton = _skeleton(intent, message, turn, obj)
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
        "object": obj,
        "skeleton": skeleton,
        "topic": topic,
        "style": voice["style"],
        "reaction": voice["reaction"],
        "affection_delta": aff,
        "trust_delta": trust,
    }
