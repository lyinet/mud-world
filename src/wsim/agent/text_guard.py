"""NPC 回复的确定性后处理（格式、人称、口号去重）。

设计约束：
- 不联网、不读墙钟、不使用随机数；同一输入 + 同一台账状态必得同一输出。
- `sanitize_format` 是纯函数；口号去重需要跨回复状态，由调用方显式持有
  `PhraseLedger`（跑批状态），模块不保存任何全局可变状态。
- 台账可用 `to_dict()` / `from_dict()` 持久化到 run state，回放时结果一致。

三类处理：
1. 格式：剥离 Markdown，把 `**动作**`、`*动作*`、`（动作）`、`(动作)`、`【动作】`
   统一改写成“我 + 动作”的口语短句；非动作括号只去掉括号符号。
2. 人称：识别“角色名 + 动作”的第三人称叙述，或“动作描写 + ：‘引号台词’”的
   叙述框架，改写为第一人称直接引语；无法安全改写时由调用方回退模板。
3. 口号：同一 NPC 最近 N 条回复内命中同一固定短语，第二次起用确定性备选句式替换。
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

# 同一短语在最近 10 条回复内再次出现即改写。70 条盲评里“包在我身上”出现 20 次，
# 窗口取 10 可把单批重复压到接近一次/窗口，同时不会因为隔得远而误判。
DEFAULT_PHRASE_WINDOW = 10
MAX_ACTION_CHARS = 40

# 常见动作线索。只用于“括号/加粗里的内容是不是动作”，不做语义理解。
_ACTION_CUES = re.compile(
    r"把|将|拍|插|搁|蹭|抹|敲|提|剁|按|压|擦|掂|刮|磨|劈|切|割|扯|拽|"
    r"推|拉|端|抱|挥|甩|跺|瞥|摸|挠|搓|抖|砸|拱|"
    r"咧嘴|摇头|点头|皱眉|瞪|叹|哼|笑|咳|喝|吹|"
    r"起身|伸手|抬手|低头|抬头|转身|凑近|凑|"
    r"拍了拍|蹭了蹭|敲了敲|掂了掂|剁了剁|一插|一搁|一拍|一按"
)
# 单字动作，用于“角色名 + 动作”叙述检测。
_NAME_ACTION_FRAGMENT = (
    r"把|将|拍|插|搁|蹭|抹|敲|提|剁|按|压|擦|掂|刮|磨|劈|切|割|"
    r"推|拉|端|抱|挥|甩|跺|瞥|摸|挠|搓|抖|砸|拱|笑|叹|哼|喝|"
    r"起|伸|抬|低|转|凑|瞪|皱|"
    r"咧嘴|摇头|点头|皱眉|冷笑|起身|伸手|抬手|低头|抬头|转身|凑近"
)
_NON_ACTION = re.compile(r"无设定|说明|注[：:]|例如|参考|按身份|默认")

_MARKDOWN_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_MARKDOWN_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.S)
_MARKDOWN_UNDERSCORE = re.compile(r"__(.+?)__", re.S)
_MARKDOWN_CODE = re.compile(r"`+([^`]*)`+")
_CJK_BRACKET = re.compile(r"【([^】]+)】")
_PAREN = re.compile(r"[（(]([^（）()]+)[）)]")
_MULTI_SPACE = re.compile(r"[ \t\u3000]+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([，。！？；：、])")

# 默认固定短语及备选句式。备选均不含原短语，按出现次序轮换（确定性，无随机）。
DEFAULT_PHRASE_REWRITES: dict[str, tuple[str, ...]] = {
    "包在我段五斗身上": ("我段五斗应下了", "我段五斗替你办", "交给我段五斗来办"),
    "包在五斗身上": ("我应下了", "我替你办", "交给我来办"),
    "包在我身上": ("我应下了", "我替你办", "交给我来办", "我记在心上了"),
    "一刀下去，斤两不会错": ("刀口上见真章", "斤两都在秤上", "我下刀有准头"),
    "斤两不会错": ("斤两准得很", "秤上见分晓"),
    "说话算话": ("我从不食言", "应下就算数"),
    "白纸黑字": ("字据上写明", "写个字据"),
}
# 同一口号的人名变体归到同一个窗口计数键，避免“包在我身上/包在我段五斗身上”
# 被当成两个不同短语而漏掉重复。
_PHRASE_CANONICAL = {
    "包在我段五斗身上": "包在我身上",
    "包在五斗身上": "包在我身上",
}


def _canonical_phrase(phrase: str) -> str:
    return _PHRASE_CANONICAL.get(phrase, phrase)


@dataclass(frozen=True)
class SanitizeResult:
    """一次后处理的结果；`flags` 便于报告与埋点，不含随机/时间信息。"""

    text: str
    original: str
    flags: tuple[str, ...] = ()
    repeated_phrase: str = ""

    @property
    def changed(self) -> bool:
        return self.text != self.original


@dataclass
class PhraseLedger:
    """每 NPC 的“最近使用短语”台账（跑批内）。

    - `window`：只看最近 N 条回复；默认 10。
    - `phrase_rewrites`：短语 → 确定性备选句式。
    - 状态全部是 dict/deque，可序列化；不含随机、时间、全局变量。
    """

    window: int = DEFAULT_PHRASE_WINDOW
    phrase_rewrites: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_PHRASE_REWRITES)
    )
    _history: dict[str, deque[set[str]]] = field(default_factory=dict, repr=False)
    _rewrite_counts: dict[tuple[str, str], int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError("window must be >= 1")
        self.phrase_rewrites = {
            str(k): tuple(str(x) for x in v)
            for k, v in sorted(self.phrase_rewrites.items(), key=lambda kv: (-len(kv[0]), kv[0]))
            if v
        }

    def _matches(self, text: str) -> list[str]:
        return [p for p in self.phrase_rewrites if p in text]

    def seen_within(self, npc: str, phrase: str) -> bool:
        hist = self._history.get(npc)
        if not hist:
            return False
        key = _canonical_phrase(phrase)
        return any(key in entry for entry in hist)

    def observe(self, npc: str, text: str) -> None:
        """记录该条最终回复里命中的固定短语；每条回复占一个窗口槽位。"""
        hist = self._history.setdefault(npc, deque(maxlen=max(1, int(self.window))))
        hist.append({_canonical_phrase(p) for p in self._matches(text)})

    def apply(self, npc: str, text: str) -> tuple[str, str]:
        """命中窗口内重复短语则改写；返回 (新文本, 被判定重复的短语)。"""
        matched = sorted(self._matches(text), key=lambda p: (-len(p), p))
        repeated = next((p for p in matched if self.seen_within(npc, p)), "")
        if not repeated:
            self.observe(npc, text)
            return text, ""
        for phrase in matched:
            if not self.seen_within(npc, phrase):
                continue
            key = (npc, phrase)
            n = self._rewrite_counts.get(key, 0)
            alts = self.phrase_rewrites[phrase]
            replacement = alts[n % len(alts)]
            self._rewrite_counts[key] = n + 1
            text = text.replace(phrase, replacement)
        self.observe(npc, text)
        return text, repeated

    def to_dict(self) -> dict[str, object]:
        counts = {
            f"{npc}\u0000{phrase}": n
            for (npc, phrase), n in sorted(self._rewrite_counts.items())
        }
        history = {
            npc: [sorted(entry) for entry in self._history.get(npc, ())]
            for npc in sorted(self._history)
        }
        return {"window": int(self.window), "history": history, "rewrite_counts": counts}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "PhraseLedger":
        ledger = cls(window=int(payload.get("window", DEFAULT_PHRASE_WINDOW)))
        history = payload.get("history") or {}
        if isinstance(history, Mapping):
            for npc, entries in history.items():
                hist: deque[set[str]] = deque(maxlen=ledger.window)
                for entry in entries or ():
                    hist.append(set(str(x) for x in entry))
                ledger._history[str(npc)] = hist
        counts = payload.get("rewrite_counts") or {}
        if isinstance(counts, Mapping):
            for key, value in counts.items():
                npc, sep, phrase = str(key).partition("\u0000")
                if sep:
                    ledger._rewrite_counts[(npc, phrase)] = int(value)
        return ledger


def _looks_like_action(text: str) -> bool:
    body = str(text or "").strip()
    if not body or len(body) > MAX_ACTION_CHARS or _NON_ACTION.search(body):
        return False
    return bool(_ACTION_CUES.search(body))


def _action_clause(body: str, npc_names: Iterable[str] = ()) -> str:
    """把动作片段改写成“我……”的口语短句。"""
    c = str(body or "").strip(" \t\r\n，,。；;：:")
    if not c:
        return ""
    if _NON_ACTION.search(c):
        return c
    for name in sorted(set(npc_names), key=len, reverse=True):
        if name and c.startswith(name):
            c = "我" + c[len(name) :]
            break
    if not c.startswith("我"):
        if re.match(r"^(砍刀|刀|菜刀|秤|抹布)", c) and re.match(
            r"^(砍刀|刀|菜刀|秤|抹布)(往|朝|向|在|一)", c
        ):
            c = "我把" + c
        elif re.match(r"^(油手|手)", c) and re.match(r"^(油手|手)(往|朝|向|在|一)", c):
            c = "我拿" + c
        else:
            c = "我" + c
    if not c.endswith(("。", "！", "？", "…", "”")):
        c += "。"
    return c


def _replace_with_action(
    text: str,
    match: re.Match[str],
    inner: str,
    npc_names: Iterable[str],
) -> str:
    """把匹配片段替换成动作短句；根据前一个字符决定是否补逗号。"""
    clause = _action_clause(inner, npc_names)
    if not clause:
        return ""
    j = match.start() - 1
    while j >= 0 and text[j] in " \t\u3000":
        j -= 1
    prev = text[j] if j >= 0 else ""
    if not prev or prev in "。！？\n；":
        return clause
    if prev in "，,、—－:：":
        return clause
    return "，" + clause


def _strip_markdown(text: str, npc_names: Iterable[str]) -> tuple[str, list[str]]:
    flags: list[str] = []
    names = tuple(npc_names)

    def bold(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        if _looks_like_action(inner):
            flags.append("markdown_action")
            return _replace_with_action(text, m, inner, names)
        flags.append("markdown")
        return inner

    def italic(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        if _looks_like_action(inner):
            flags.append("markdown_action")
            return _replace_with_action(text, m, inner, names)
        flags.append("markdown")
        return inner

    text = _MARKDOWN_BOLD.sub(bold, text)
    text = _MARKDOWN_UNDERSCORE.sub(lambda m: m.group(1), text)
    text = _MARKDOWN_ITALIC.sub(italic, text)
    text = _MARKDOWN_CODE.sub(lambda m: m.group(1), text)
    text, n = _CJK_BRACKET.subn(
        lambda m: _action_clause(m.group(1), names)
        if _looks_like_action(m.group(1))
        else m.group(1),
        text,
    )
    if n:
        flags.append("cjk_bracket")
    # 兜底：保证输出不再含 Markdown 符号。
    if re.search(r"[*【】]", text):
        text = re.sub(r"[*【】]", "", text)
        flags.append("markdown")
    return text, flags


def _is_narrative_prefix(prefix: str, npc_names: Sequence[str]) -> bool:
    p = str(prefix or "").strip()
    if not p:
        return False
    if re.match(r"^他自己", p):
        return True
    if p in npc_names:
        return True
    if re.match(r"^我", p) and _ACTION_CUES.search(p):
        return True
    for name in npc_names:
        if name and p.startswith(name) and (_ACTION_CUES.search(p) or re.search(r"[把将]", p)):
            return True
    # “拍得胸脯油亮：”这类省略主语的旁白。
    if _ACTION_CUES.match(p):
        return True
    return False


# 只把“首句的动作描写 + ：引号”当叙述框架；前面已有完整句子时不能整段删除。
_FRAME = re.compile(r'^(?P<pre>[^“”"\n。！？]{1,120})[：:]\s*(?P<quote>["“])(?P<body>.*)$', re.S)


def _rewrite_third_person(
    text: str, npc_names: Iterable[str]
) -> tuple[str, bool, bool]:
    """返回 (新文本, 是否检测到第三人称/旁白, 是否未能安全改写)。"""
    names = tuple(sorted({n for n in npc_names if n}, key=len, reverse=True))
    detected = False
    frame = _FRAME.match(text)
    if frame and _is_narrative_prefix(frame.group("pre"), names):
        detected = True
        text = frame.group("quote") + frame.group("body").lstrip()
    if names:
        alt = "|".join(re.escape(n) for n in names)
        name_sentence = re.compile(
            rf"(^|[。！？\n])\s*(?:{alt})(?=\s*(?:[把将]|{_NAME_ACTION_FRAGMENT}))"
        )
        text, n = name_sentence.subn(lambda m: m.group(1) + "我", text)
        if n:
            detected = True
    self_sentence = re.compile(r"(^|[。！？\n])\s*他自己")
    text, n = self_sentence.subn(lambda m: m.group(1) + "我", text)
    if n:
        detected = True
    unresolved = False
    if detected:
        if names:
            alt = "|".join(re.escape(n) for n in names)
            unresolved = bool(
                re.search(rf"(^|[。！？\n])\s*(?:{alt})(?=\s*(?:[把将]|{_NAME_ACTION_FRAGMENT}))", text)
            )
        unresolved = unresolved or bool(re.search(r"(^|[。！？\n])\s*他自己", text))
    return text, detected, unresolved


def _replace_parentheticals(text: str, npc_names: Iterable[str]) -> tuple[str, list[str]]:
    flags: list[str] = []
    names = tuple(npc_names)

    def repl(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        if not inner:
            return ""
        if _looks_like_action(inner):
            flags.append("action_mark")
            return _replace_with_action(text, m, inner, names)
        flags.append("parenthetical")
        j = m.start() - 1
        while j >= 0 and text[j] in " \t\u3000":
            j -= 1
        prev = text[j] if j >= 0 else ""
        if prev and prev not in "。！？\n；，,、—－:：":
            return "，" + inner
        return inner

    text = _PAREN.sub(repl, text)
    # 失配的半角括号也去掉，避免输出残留 (动作) 观感。
    text = text.replace("(", "").replace(")", "")
    return text, flags


def _normalize_quotes(text: str) -> str:
    out: list[str] = []
    opening = True
    for ch in text:
        if ch == '"':
            out.append("“" if opening else "”")
            opening = not opening
        else:
            out.append(ch)
    return "".join(out)


def _cleanup(text: str) -> str:
    text = _MULTI_SPACE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = re.sub(r"[。]{2,}", "。", text)
    text = re.sub(r"([，。！？；：、])\s+", r"\1", text)
    text = re.sub(r"[ ]*([，。！？；：])", r"\1", text)
    return text.strip(" \t\r\n，,")


def sanitize_format(text: str, *, npc_names: Iterable[str] = ()) -> tuple[str, tuple[str, ...], bool]:
    """纯函数：修格式与人称。返回 (新文本, flags, 是否未解决第三人称)。"""
    names = tuple(str(n) for n in npc_names if str(n))
    flags: list[str] = []
    body = str(text or "").strip()
    body, f = _strip_markdown(body, names)
    flags.extend(f)
    body, detected, unresolved = _rewrite_third_person(body, names)
    if detected:
        flags.append("third_person")
    body, f = _replace_parentheticals(body, names)
    flags.extend(f)
    body = _normalize_quotes(body)
    body = _cleanup(body)
    # flags 去重且保持出现顺序，便于测试与埋点稳定比较。
    return body, tuple(dict.fromkeys(flags)), unresolved


def sanitize_reply(
    text: str,
    *,
    npc: str = "",
    npc_names: Iterable[str] = (),
    ledger: PhraseLedger | None = None,
    fallback: str | None = None,
) -> SanitizeResult:
    """完整后处理入口。

    `ledger` 由调用方按跑批创建并持久化；不传则只做格式/人称处理。
    若第三人称无法安全改写成第一人称且给了 `fallback`，直接回退模板并标
    `fallback_template`（模板本身也会先过一遍格式清洗）。
    """
    original = str(text or "")
    cleaned, flags, unresolved = sanitize_format(original, npc_names=npc_names)
    if unresolved:
        if fallback is not None:
            fb, _fb_flags, _ = sanitize_format(fallback, npc_names=npc_names)
            return SanitizeResult(
                text=fb, original=original, flags=tuple(dict.fromkeys(flags + ("fallback_template",)))
            )
        flags = tuple(dict.fromkeys(flags + ("third_person_unresolved",)))
    repeated = ""
    if ledger is not None and cleaned:
        cleaned, repeated = ledger.apply(npc or "-", cleaned)
        if repeated:
            flags = tuple(dict.fromkeys(flags + ("phrase_repeated",)))
    return SanitizeResult(
        text=cleaned, original=original, flags=flags, repeated_phrase=repeated
    )
