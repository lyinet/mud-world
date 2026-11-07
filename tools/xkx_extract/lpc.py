"""LPC 源码的最小字面量解析器。

范围刻意收窄：只解析 xkx 内容文件里实际出现的写法
（字符串、heredoc、数字、数组 `({...})`、映射 `([...])`、
路径宏、颜色宏、函数指针 `(: name :)`、未知表达式）。

不做完整语法分析，也不做语义求值；解析不出来的值统一落成 Expr，
由调用方决定是丢弃还是写进 diagnostics。
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

# 颜色宏：内容里只用于终端着色，抽取时必须剥掉。
COLOR_MACROS = {
    "BLK", "RED", "GRN", "YEL", "BLU", "MAG", "CYN", "WHT",
    "HIR", "HIG", "HIY", "HIB", "HIM", "HIC", "HIW",
    "HBRED", "HBGRN", "HBYEL", "HBBLU", "HBMAG", "HBCYN", "HBWHT",
    "BBLK", "BRED", "BGRN", "BYEL", "BBLU", "BMAG", "BCYN",
    "NOR", "BOLD", "CLR", "HOME", "REF", "BIGTOP", "BIGBOT",
    "SAVEC", "REST", "SINGW", "DBL", "FRTOP", "FRBOT", "UNFR",
    "BLINK", "U", "REV", "HIREV", "ESC", "CSI", "BEL",
}

# 目录宏：值直接来自 include/globals.h，这里静态抄一份，避免运行时依赖源码布局。
DIR_MACROS = {
    "ARMOR_DIR": "/clone/armor/",
    "WEAPON_DIR": "/clone/weapon/",
    "BOOK_DIR": "/clone/book/",
    "MONEY_DIR": "/clone/money/",
    "ANIMAL_DIR": "/clone/animal/",
    "JEWELRY_DIR": "/clone/jewelry/",
    "FOOD_DIR": "/clone/food/",
    "FRUIT_DIR": "/clone/food/fruit/",
    "LIQUID_DIR": "/clone/food/liquid/",
    "TEA_DIR": "/clone/food/tea/",
    "FODDER_DIR": "/clone/food/fodder/",
    "CLOTH_DIR": "/clone/cloth/",
    "HEAD_DIR": "/clone/cloth/head/",
    "BOOT_DIR": "/clone/cloth/boot/",
    "NOSTRUM_DIR": "/clone/medicine/nostrum/",
    "POISON_DIR": "/clone/medicine/poison/",
    "VEGETABLE_DIR": "/clone/medicine/vegetable/",
    "SHOP_DIR": "/clone/shop/",
    "INC_DIR": "/include/",
    "COMMAND_DIR": "/cmds/",
    "DATA_DIR": "/data/",
    "HELP_DIR": "/help/",
    "LOG_DIR": "/log/",
    "COIN_OB": "/clone/money/coin",
    "GOLD_OB": "/clone/money/gold",
    "SILVER_OB": "/clone/money/silver",
}

# 带一个参数、拼成路径的宏。
PATH_FUNC_MACROS = {
    "CLASS_D": ("/kungfu/class/", ""),
    "SKILL_D": ("/kungfu/skill/", ""),
    "CONDITION_D": ("/kungfu/condition/", ""),
    "QUESTH_D": ("/quest/qhlist", ""),
    "QUESTS_D": ("/quest/qslist", ""),
    "QUESTW_D": ("/quest/qwlist", ""),
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class Expr:
    """无法静态求值的表达式。保留源码形态，便于诊断。"""

    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Expr({self.text!r})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Expr) and other.text == self.text

    def __hash__(self) -> int:
        return hash((Expr, self.text))


@dataclass(frozen=True)
class Token:
    kind: str  # STR / HEREDOC / NUM / IDENT / PUNCT
    value: object
    line: int


@dataclass
class ParseContext:
    """一个文件的解析上下文。"""

    file_path: str  # mudlib 绝对路径（不带扩展名），如 /d/hangzhou/dadao1
    dir_path: str  # mudlib 目录，带结尾斜杠
    defines: dict  # 文件内 #define
    depth: int = 0


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def clean_text(value) -> object:
    """把描述类文本收敛成可入库的形态：去 ANSI、统一换行、裁掉行尾空白。"""
    if not isinstance(value, str):
        return value
    value = strip_ansi(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in value.split("\n")]
    return "\n".join(lines).strip()


def mudlib_id(rel_path: str) -> str:
    """仓库相对路径 -> mudlib id。"""
    rel = rel_path.replace("\\", "/")
    if rel.endswith(".lpc"):
        rel = rel[:-4]
    return "/" + rel.lstrip("/")


def mudlib_dir(rel_path: str) -> str:
    """文件所在目录的 mudlib 路径，带结尾斜杠。"""
    rel = rel_path.replace("\\", "/").lstrip("/")
    d = posixpath.dirname(rel)
    return "/" + (d + "/" if d else "")


# ---------------------------------------------------------------------------
# 词法
# ---------------------------------------------------------------------------


_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
_HEREDOC_TERM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def lex(text: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(text)
    line = 1
    while i < n:
        ch = text[i]
        if ch in " \t\r\f\v":
            i += 1
            continue
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            if j < 0:
                line += text.count("\n", i)
                i = n
            else:
                line += text.count("\n", i, j + 2)
                i = j + 2
            continue
        if ch == '"':
            start_line = line
            buf: list[str] = []
            j = i + 1
            while j < n:
                c = text[j]
                if c == "\\":
                    nxt = text[j + 1] if j + 1 < n else ""
                    buf.append(_SIMPLE_ESCAPES.get(nxt, nxt))
                    j += 2
                    continue
                if c == '"':
                    break
                if c == "\n":
                    line += 1
                buf.append(c)
                j += 1
            tokens.append(Token("STR", "".join(buf), start_line))
            i = j + 1
            continue
        if ch == "@":
            start_line = line
            j = i + 1
            m = _HEREDOC_TERM_RE.match(text, j)
            if not m:
                tokens.append(Token("PUNCT", "@", line))
                i += 1
                continue
            tag = m.group(0)
            j = m.end()
            # 跳到行尾
            k = j
            while k < n and text[k] not in "\r\n":
                k += 1
            if k < n and text[k] == "\r":
                k += 1
            if k < n and text[k] == "\n":
                k += 1
                line += 1
            body: list[str] = []
            while k < n:
                eol = k
                while eol < n and text[eol] not in "\r\n":
                    eol += 1
                raw = text[k:eol]
                stripped = raw.strip()
                if stripped == tag or (
                    stripped.startswith(tag)
                    and len(stripped) > len(tag)
                    and not (stripped[len(tag)].isalnum() or stripped[len(tag)] == "_")
                ):
                    # 定位到 tag 之后，把 tag 所在行的剩余符号留给词法器
                    tag_at = k + raw.index(tag)
                    k = tag_at + len(tag)
                    break
                body.append(raw)
                k = eol
                if k < n and text[k] == "\r":
                    k += 1
                if k < n and text[k] == "\n":
                    k += 1
                    line += 1
            tokens.append(Token("HEREDOC", "\n".join(body), start_line))
            i = k
            continue
        if ch.isdigit():
            j = i
            while j < n and text[j].isdigit():
                j += 1
            tokens.append(Token("NUM", int(text[i:j]), line))
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(Token("IDENT", text[i:j], line))
            i = j
            continue
        if ch == "-" and i + 1 < n and text[i + 1] == ">":
            tokens.append(Token("PUNCT", "->", line))
            i += 2
            continue
        tokens.append(Token("PUNCT", ch, line))
        i += 1
    return tokens


# ---------------------------------------------------------------------------
# 语法
# ---------------------------------------------------------------------------


def _render(value) -> str:
    if isinstance(value, Expr):
        return value.text
    if isinstance(value, str):
        return '"' + value + '"'
    return str(value)


def _concat(a, b):
    if isinstance(a, Expr) or isinstance(b, Expr):
        return Expr(_render(a) + " + " + _render(b))
    if isinstance(a, int) and isinstance(b, int):
        return a + b
    if isinstance(a, str) and isinstance(b, str):
        return a + b
    if isinstance(a, str):
        return a + (b if isinstance(b, str) else str(b))
    if isinstance(b, str):
        return (a if isinstance(a, str) else str(a)) + b
    return Expr(_render(a) + " + " + _render(b))


def extract_defines(text: str) -> dict:
    """抓取文件内 `#define NAME value`。

    只处理单行（含反斜杠续行），不展开 #ifdef 分支，够用即可。
    """
    joined = re.sub(r"\\\r?\n[ \t]*", " ", text)
    defines: dict[str, str] = {}
    for m in re.finditer(r"^[ \t]*#define[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]+(.+)$", joined, re.M):
        defines[m.group(1)] = m.group(2).strip()
    return defines


def _expand_ident(name: str, ctx: ParseContext):
    if name in ctx.defines and ctx.depth < 8:
        raw = ctx.defines[name]
        sub = ParseContext(ctx.file_path, ctx.dir_path, ctx.defines, ctx.depth + 1)
        try:
            return parse_macro_value(raw, sub)
        except ParseError:
            return Expr(raw)
    if name == "__DIR__":
        return ctx.dir_path
    if name == "__FILE__":
        return ctx.file_path
    if name in DIR_MACROS:
        return DIR_MACROS[name]
    if name in COLOR_MACROS:
        return ""
    return Expr(name)


def _expand_call(name: str, args: list, ctx: ParseContext):
    if name in PATH_FUNC_MACROS and len(args) >= 1 and isinstance(args[0], str):
        prefix, suffix = PATH_FUNC_MACROS[name]
        return prefix + args[0] + suffix
    rendered = ", ".join(_render(a) for a in args)
    return Expr(f"{name}({rendered})")


class ParseError(Exception):
    pass


_ATOM_START_PUNCT = ("(",)


def _is_atom_start(tok: Token) -> bool:
    if tok.kind in ("STR", "HEREDOC", "NUM", "IDENT"):
        return True
    return tok.kind == "PUNCT" and tok.value in _ATOM_START_PUNCT


def parse_value(tokens: list[Token], i: int, ctx: ParseContext):
    """解析一个表达式，返回 (python 值, 下一个 token 下标)。"""
    value, i = _parse_primary(tokens, i, ctx)
    if isinstance(value, (list, dict)):
        return value, i
    while i < len(tokens):
        tok = tokens[i]
        if tok.kind == "PUNCT" and tok.value == "->":
            # 链式调用（如 me->query("id")）无法静态求值，整段收敛成 Expr。
            j = i + 1
            if j < len(tokens) and tokens[j].kind == "IDENT":
                member = tokens[j].value
                j += 1
                if (
                    j + 1 < len(tokens)
                    and tokens[j].kind == "PUNCT"
                    and tokens[j].value == "("
                ):
                    args, j = _parse_args(tokens, j + 1, ctx)
                    value = Expr(_render(value) + "->" + member + "(" + ", ".join(_render(a) for a in args) + ")")
                else:
                    value = Expr(_render(value) + "->" + member)
                i = j
                continue
            i += 1
            continue
        if tok.kind == "PUNCT" and tok.value == "+":
            right, i = _parse_primary(tokens, i + 1, ctx)
            value = _concat(value, right)
            continue
        # LPC 支持相邻字符串常量拼接：`__DIR__ "road5"`、`HIR "红花会" NOR`。
        if isinstance(value, str) and _is_atom_start(tok):
            right, i = _parse_primary(tokens, i, ctx)
            value = _concat(value, right)
            continue
        break
    return value, i


def _parse_primary(tokens: list[Token], i: int, ctx: ParseContext):
    if i >= len(tokens):
        raise ParseError("表达式意外结束")
    tok = tokens[i]
    if tok.kind in ("STR", "HEREDOC"):
        return tok.value, i + 1
    if tok.kind == "NUM":
        return tok.value, i + 1
    if tok.kind == "PUNCT" and tok.value == "-":
        value, j = _parse_primary(tokens, i + 1, ctx)
        return (-value if isinstance(value, int) else Expr("-" + _render(value))), j
    if tok.kind == "PUNCT" and tok.value == "(":
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is not None and nxt.kind == "PUNCT" and nxt.value == ":":
            return _parse_func_pointer(tokens, i + 2)
        if nxt is not None and nxt.kind == "PUNCT" and nxt.value == "[":
            return _parse_mapping(tokens, i + 2, ctx)
        if nxt is not None and nxt.kind == "PUNCT" and nxt.value == "{":
            return _parse_array(tokens, i + 2, ctx)
        value, j = parse_value(tokens, i + 1, ctx)
        if j < len(tokens) and tokens[j].kind == "PUNCT" and tokens[j].value == ")":
            j += 1
        return value, j
    if tok.kind == "IDENT":
        if i + 1 < len(tokens) and tokens[i + 1].kind == "PUNCT" and tokens[i + 1].value == "(":
            args, j = _parse_args(tokens, i + 2, ctx)
            return _expand_call(tok.value, args, ctx), j
        return _expand_ident(tok.value, ctx), i + 1
    raise ParseError(f"无法解析的 token: {tok.kind} {tok.value!r} (line {tok.line})")


def _parse_func_pointer(tokens: list[Token], i: int):
    parts: list[str] = []
    while i < len(tokens):
        tok = tokens[i]
        if tok.kind == "PUNCT" and tok.value == ":":
            i += 1
            break
        parts.append(str(tok.value))
        i += 1
    if i < len(tokens) and tokens[i].kind == "PUNCT" and tokens[i].value == ")":
        i += 1
    return Expr("(: " + " ".join(parts) + " :)"), i


def _parse_array(tokens: list[Token], i: int, ctx: ParseContext):
    items: list = []
    while i < len(tokens):
        tok = tokens[i]
        if tok.kind == "PUNCT" and tok.value == "}":
            i += 1
            break
        if tok.kind == "PUNCT" and tok.value == ",":
            i += 1
            continue
        value, i = parse_value(tokens, i, ctx)
        items.append(value)
    if i < len(tokens) and tokens[i].kind == "PUNCT" and tokens[i].value == ")":
        i += 1
    return items, i


def _parse_mapping(tokens: list[Token], i: int, ctx: ParseContext):
    result: dict = {}
    while i < len(tokens):
        tok = tokens[i]
        if tok.kind == "PUNCT" and tok.value == "]":
            i += 1
            break
        if tok.kind == "PUNCT" and tok.value == ",":
            i += 1
            continue
        key, i = parse_value(tokens, i, ctx)
        if i < len(tokens) and tokens[i].kind == "PUNCT" and tokens[i].value == ":":
            i += 1
        else:
            raise ParseError("映射缺少 ':'")
        value, i = parse_value(tokens, i, ctx)
        if isinstance(key, str):
            result[key] = value
        elif isinstance(key, Expr):
            result["<expr:" + key.text + ">"] = value
        else:
            result[str(key)] = value
    if i < len(tokens) and tokens[i].kind == "PUNCT" and tokens[i].value == ")":
        i += 1
    return result, i


def _parse_args(tokens: list[Token], i: int, ctx: ParseContext):
    args: list = []
    while i < len(tokens):
        tok = tokens[i]
        if tok.kind == "PUNCT" and tok.value == ")":
            i += 1
            break
        if tok.kind == "PUNCT" and tok.value == ",":
            i += 1
            continue
        value, i = parse_value(tokens, i, ctx)
        args.append(value)
    return args, i


def parse_macro_value(raw: str, ctx: ParseContext):
    tokens = lex(raw)
    if not tokens:
        return ""
    value, i = parse_value(tokens, 0, ctx)
    return value


# ---------------------------------------------------------------------------
# 顶层调用扫描
# ---------------------------------------------------------------------------

# 只关心这些 efun/函数调用；其余原样跳过。
RECORDED_CALLS = {
    "set",
    "set_name",
    "set_skill",
    "set_temp",
    "map_skill",
    "prepare_skill",
    "carry_object",
    "add_money",
    "set_weight",
}


def scan_calls(text: str, ctx: ParseContext) -> tuple[list[tuple], list[dict]]:
    """扁平扫描文件里的目标调用。

    返回 (calls, errors)。call 形如 (name, args, line, chained_method)。
    条件分支里的 set() 也会被记录；调用方自己决定取哪个分支的值。
    """
    tokens = lex(text)
    calls: list[tuple] = []
    errors: list[dict] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        # 跳过 `this_player()->set(...)`、`::set(...)` 这类链式/父类调用，
        # 否则会把玩家运行时状态误当成模板属性。
        chained = False
        if i > 0 and tokens[i - 1].kind == "PUNCT" and tokens[i - 1].value == "->":
            chained = True
        if (
            i > 1
            and tokens[i - 1].kind == "PUNCT"
            and tokens[i - 1].value == ":"
            and tokens[i - 2].kind == "PUNCT"
            and tokens[i - 2].value == ":"
        ):
            chained = True
        if (
            not chained
            and tok.kind == "IDENT"
            and tok.value in RECORDED_CALLS
            and i + 1 < len(tokens)
            and tokens[i + 1].kind == "PUNCT"
            and tokens[i + 1].value == "("
        ):
            try:
                args, j = _parse_args(tokens, i + 2, ctx)
            except ParseError as exc:
                errors.append({"line": tok.line, "message": str(exc)})
                i += 1
                continue
            method = None
            if (
                j + 1 < len(tokens)
                and tokens[j].kind == "PUNCT"
                and tokens[j].value == "->"
                and tokens[j + 1].kind == "IDENT"
            ):
                method = tokens[j + 1].value
                j += 2
                if (
                    j + 1 < len(tokens)
                    and tokens[j].kind == "PUNCT"
                    and tokens[j].value == "("
                    and tokens[j + 1].kind == "PUNCT"
                    and tokens[j + 1].value == ")"
                ):
                    j += 2
            calls.append((tok.value, args, tok.line, method))
            i = j
            continue
        i += 1
    return calls, errors
