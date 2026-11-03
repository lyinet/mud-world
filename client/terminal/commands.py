"""经典 MUD 风格的命令层（纯文本，无框架）。

所有输出按终端列宽排版：中文按 2 列宽计算，保证中文/英文混排对齐。
命令只读世界快照；`talk` 仅提交 propose_intent，不写世界。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

from runview import PLAYER_ID, PLAYER_NAME, RunView, RunError, clock_label

MAX_MESSAGE = 200
VERB_GROUP = {"trade", "marry"}


# ------------------------------------------------------------------ 列宽工具
def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def disp_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def pad_right(text: str, width: int) -> str:
    return text + " " * max(0, width - disp_width(text))


def fit(text: str, width: int) -> str:
    """按终端列宽截断，超出用单列省略号。"""
    if disp_width(text) <= width:
        return pad_right(text, width)
    out: list[str] = []
    used = 0
    for ch in text:
        w = char_width(ch)
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…" + " " * max(0, width - used - 1)


def table(headers: list[str], rows: list[list[str]], widths: list[int]) -> list[str]:
    out = ["  " + "  ".join(fit(h, widths[i]) for i, h in enumerate(headers)).rstrip()]
    out.append("  " + "  ".join("-" * widths[i] for i in range(len(widths))).rstrip())
    for row in rows:
        out.append("  " + "  ".join(fit(cell, widths[i]) for i, cell in enumerate(row)).rstrip())
    return out


# ------------------------------------------------------------------ 结果结构
@dataclass
class Result:
    lines: list[str]
    kind: str = "info"  # info / notice / error
    meta: dict[str, Any] = field(default_factory=dict)


def _err(*lines: str) -> Result:
    return Result(list(lines), kind="error")


# ------------------------------------------------------------------ 会话
class TerminalSession:
    """一个终端会话：维护客户端坐标，世界状态只读。"""

    def __init__(self, view: RunView, start: str = ""):
        self.view = view
        self.position = ""
        if view.error is None:
            wanted = view.resolve_location(start) if start else None
            self.position = wanted or ("market" if "market" in view.locations else sorted(view.locations)[0])

    # ---------------------------------------------------------------- dispatch
    def handle(self, line: str) -> Result:
        raw = (line or "").strip()
        if not raw:
            return Result([], kind="info")
        if self.view.error is not None:
            if raw.split()[0].lower() == "help":
                return Result(["世界不可用，命令帮助：", *_HELP_LINES], kind="notice")
            return _err("世界不可用：" + self.view.error.message, "提示：" + self.view.error.hint)

        parts = raw.split()
        cmd = parts[0].lower()
        args = parts[1:]
        handlers = {
            "look": self.cmd_look,
            "go": self.cmd_go,
            "who": self.cmd_who,
            "status": self.cmd_status,
            "quests": self.cmd_quests,
            "talk": self.cmd_talk,
            "help": self.cmd_help,
        }
        handler = handlers.get(cmd)
        if handler is None:
            return _err(
                f"未知命令：{cmd}",
                "输入 help 查看可用命令（look / go / who / status / quests / talk / help）。",
            )
        try:
            return handler(args)
        except RunError as exc:
            return _err("世界不可用：" + exc.message, "提示：" + exc.hint)
        except Exception as exc:  # 任何意外都只回一句人话
            return _err(f"命令执行失败：{type(exc).__name__}: {exc}")

    @property
    def prompt(self) -> str:
        loc = self.view.loc_name(self.position) if self.position else "?"
        return f"{PLAYER_NAME}@{loc}> "

    def _meta(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "tick": self.view.tick,
            "clock": clock_label(self.view.tick),
            "prompt": self.prompt,
            "outbox": self.view.outbox_count,
        }

    def _result(self, lines: list[str], kind: str = "info") -> Result:
        return Result(lines, kind=kind, meta=self._meta())

    # ---------------------------------------------------------------- commands
    def cmd_help(self, args: list[str]) -> Result:
        lines = ["世界审计器 · 经典终端客户端（只读外壳）", *_HELP_LINES]
        return self._result(lines, kind="notice")

    def cmd_look(self, args: list[str]) -> Result:
        snap = self.view.snapshot()
        loc = self.position
        name = self.view.loc_name(loc)
        lines = [f"【{name}】", f"世界时间：{clock_label(self.view.tick)}（tick {self.view.tick}）"]

        exits = self.view.loc_exits(loc)
        exit_text = "、".join(self.view.loc_name(x) for x in exits) if exits else "无"
        lines.append(f"出口：{exit_text}")

        people = self.view.npcs_at(snap, loc)
        if people:
            lines.append(f"在场人物（{len(people)}）：")
            width = max(disp_width(str(p.get("name") or "")) for p in people) + 2
            for p in people:
                lines.append("  " + pad_right(str(p.get("name") or ""), width) + f"（{p.get('role') or '未知'}）")
        else:
            lines.append("在场人物：无")

        stock: dict[str, int] = {}
        for biz in (snap.get("businesses") or {}).values():
            if str(biz.get("location") or "") != loc:
                continue
            for iid, qty in (biz.get("stock") or {}).items():
                stock[str(iid)] = stock.get(str(iid), 0) + int(qty or 0)
        if stock:
            goods = "、".join(
                f"{self.view.item_name(iid)}×{qty}" for iid, qty in sorted(stock.items()) if qty > 0
            )
            lines.append(f"可见物品（商铺货架）：{goods}")
        else:
            lines.append("可见物品：无（这里没有商铺货架，也没有地面掉落物）")
        return self._result(lines)

    def cmd_go(self, args: list[str]) -> Result:
        if not args:
            exits = self.view.loc_exits(self.position)
            if not exits:
                return _err(f"{self.view.loc_name(self.position)}没有可走的出口。")
            listing = "、".join(self.view.loc_name(x) for x in exits)
            return _err("用法：go <地点>", f"当前可走：{listing}")

        token = " ".join(args).strip()
        dest = self.view.resolve_location(token)
        if dest is None:
            exits = self.view.loc_exits(self.position)
            listing = "、".join(self.view.loc_name(x) for x in exits) if exits else "无"
            return _err(
                f"地点不存在：{token}",
                f"当前可走：{listing}。可用 look 查看出口，或用 who 查看全图。",
            )
        if dest == self.position:
            return _err(f"你已经在{self.view.loc_name(dest)}。")

        exits = self.view.loc_exits(self.position)
        if dest not in exits:
            listing = "、".join(self.view.loc_name(x) for x in exits) if exits else "无"
            return _err(
                f"走不过去：{self.view.loc_name(self.position)} 不直接通往 {self.view.loc_name(dest)}。",
                f"从{self.view.loc_name(self.position)}能走：{listing}。",
            )

        origin = self.position
        self.position = dest
        lines = [
            f"你从{self.view.loc_name(origin)}走到{self.view.loc_name(dest)}。",
            "出口：" + ("、".join(self.view.loc_name(x) for x in self.view.loc_exits(dest)) or "无"),
            "（客户端坐标已更新；世界里的 NPC、事件日志与账本没有变化。）",
        ]
        return self._result(lines, kind="notice")

    def cmd_who(self, args: list[str]) -> Result:
        snap = self.view.snapshot()
        entities = snap.get("entities") or {}
        rows_raw = [
            (str(ent.get("location") or ""), str(ent.get("name") or ""), str(ent.get("role") or ""))
            for ent in entities.values()
            if str(ent.get("status") or "") == "alive"
        ]
        rows_raw.sort(key=lambda x: (self.view.loc_name(x[0]), x[1]))
        if not rows_raw:
            return self._result(["世界里没有在线人物。"])

        rows = [[self.view.loc_name(loc), name, role] for loc, name, role in rows_raw]
        widths = [10, 10, 8]
        lines = [f"在线人物（{len(rows)} 人，世界时间 {clock_label(self.view.tick)}）："]
        lines.extend(table(["位置", "姓名", "职业"], rows, widths))
        return self._result(lines)

    def cmd_status(self, args: list[str]) -> Result:
        snap = self.view.snapshot()
        entities = snap.get("entities") or {}
        accounts = snap.get("accounts") or {}
        goals = snap.get("goals") or {}
        ent = entities.get(PLAYER_ID)

        lines = [
            f"角色：{PLAYER_NAME}（{PLAYER_ID}，客户端视角；世界状态中{'已' if ent else '未'}注册）",
            f"位置：{self.view.loc_name(self.position)}（客户端本地坐标）",
            f"世界时间：{clock_label(self.view.tick)}（tick {self.view.tick}）",
        ]
        wallet = accounts.get(f"wallet:{PLAYER_ID}")
        if wallet is None:
            lines.append("钱：未登记（世界账本中没有你的钱包账户）")
        else:
            lines.append(f"钱：{int(wallet)} 文")
        if ent is None:
            lines.append("物品：未登记（世界状态里没有你的随身物品）")
            lines.append("目标：无（世界状态里没有你的目标）")
        else:
            inv = ent.get("inventory") or {}
            items = "、".join(f"{self.view.item_name(i)}×{q}" for i, q in sorted(inv.items()) if q > 0)
            lines.append("物品：" + (items or "无"))
            mine = [g for g in goals.values() if str(g.get("npc") or "") == PLAYER_ID]
            if not mine:
                lines.append("目标：无")
            else:
                for g in mine:
                    lines.append(
                        f"目标：{g.get('content_type') or g.get('kind')} "
                        f"{float(g.get('progress') or 0):.1f}/{float(g.get('target') or 1):.1f} "
                        f"（{g.get('status')}）"
                    )
        lines.append(f"待落地提案：{self.view.outbox_count} 条（仅在本客户端内存 outbox，未写入世界）")
        return self._result(lines)

    def cmd_quests(self, args: list[str]) -> Result:
        snap = self.view.snapshot()
        entities = snap.get("entities") or {}
        raw = snap.get("quests") or {}
        quests = list(raw.values()) if isinstance(raw, dict) else list(raw)
        now = self.view.tick

        open_quests = [q for q in quests if str(q.get("status") or "") in ("open", "accepted")]
        counts = {
            "open": sum(1 for q in quests if str(q.get("status") or "") == "open"),
            "accepted": sum(1 for q in quests if str(q.get("status") or "") == "accepted"),
            "completed": sum(1 for q in quests if str(q.get("status") or "") == "completed"),
            "failed": sum(1 for q in quests if str(q.get("status") or "") == "failed"),
            "expired": sum(1 for q in quests if str(q.get("status") or "") == "expired"),
        }
        head = (
            f"任务板（世界时间 {clock_label(now)}）："
            f"挂出 {counts['open']} · 已被接 {counts['accepted']} · "
            f"已完成 {counts['completed']} · 失败 {counts['failed']} · 过期 {counts['expired']}"
        )
        if not open_quests:
            return self._result(
                [head, "当前没有挂出的任务。已完成的单据不再占用任务板。"], kind="notice"
            )

        def sort_key(q: dict[str, Any]) -> tuple:
            giver_ent = entities.get(str(q.get("giver") or "")) or {}
            status = str(q.get("status") or "")
            deadline = int(q.get("deadline_tick") or 0)
            acceptable = status == "open" and now <= deadline and str(giver_ent.get("status") or "") == "alive"
            return (0 if acceptable else 1, status != "open", deadline)

        lines = [head]
        for q in sorted(open_quests, key=sort_key):
            giver_id = str(q.get("giver") or "")
            giver_ent = entities.get(giver_id) or {}
            giver_name = str(giver_ent.get("name") or giver_id)
            giver_loc = str(giver_ent.get("location") or "")
            giver_loc_name = self.view.loc_name(giver_loc) if giver_loc else "未知"
            target_npc = str(q.get("target_npc") or "")
            target_loc = str(q.get("target_location") or "")
            needs_together = bool(target_npc) and str(q.get("verb") or "") in VERB_GROUP
            status = str(q.get("status") or "")
            deadline = int(q.get("deadline_tick") or 0)
            deadline_day = deadline // 144 + 1 if deadline else 0
            acceptable = status == "open" and now <= deadline and str(giver_ent.get("status") or "") == "alive"

            if status == "accepted":
                tag = "[已接]"
                hint = f"已被{self.view.npc_name(snap, str(q.get('acceptor') or ''))}接走"
            elif acceptable:
                tag = "[可接]"
                hint = f"先去{giver_loc_name}找{giver_name}接单"
                if needs_together:
                    hint += f"，接单时需与{self.view.npc_name(snap, target_npc)}同行"
            elif status == "open" and now > deadline:
                tag = "[过期]"
                hint = f"已过截止日（第 {deadline_day} 天），等{giver_name}发下一张"
            else:
                tag = "[不可接]"
                hint = f"发布人{giver_name}当前不可用"

            name = self.view.quest_name(str(q.get("template_id") or ""))
            target_parts = []
            if target_loc:
                target_parts.append(self.view.loc_name(target_loc))
            if target_npc:
                target_parts.append(self.view.npc_name(snap, target_npc))
            target_text = " / ".join(target_parts) or "无指定地点"
            lines.append(f"{tag} {name}（{q.get('template_id')}）")
            lines.append(
                f"     发布人：{giver_name} @ {giver_loc_name}   同行：{'是' if needs_together else '否'}   "
                f"截止：第 {deadline_day} 天   报酬：{int(q.get('reward_money') or 0)} 文"
            )
            lines.append(f"     目标：{target_text}")
            lines.append(f"     提示：{hint}")
        return self._result(lines)

    def cmd_talk(self, args: list[str]) -> Result:
        if not args:
            return _err("用法：talk <人名> <一句话>", "例：talk 阿茗 今天的茶是新沏的吗？")
        npc_token = args[0]
        message = " ".join(args[1:]).strip()
        if not message:
            return _err("用法：talk <人名> <一句话>", "例：talk 阿茗 今天的茶是新沏的吗？")
        if len(message) > MAX_MESSAGE:
            return _err(f"一次最多说 {MAX_MESSAGE} 字，你说了 {len(message)} 字。")

        snap = self.view.snapshot()
        npc_id = self.view.resolve_npc(npc_token, snap)
        if npc_id is None:
            return _err(f"找不到这个人：{npc_token}", "用 who 查看全世界在线人物，或 look 查看在场人物。")
        ent = (snap.get("entities") or {}).get(npc_id) or {}
        name = str(ent.get("name") or npc_id)
        status = str(ent.get("status") or "")
        if status != "alive":
            return _err(f"{name}已经不在了（status={status}），没法搭话。")
        npc_loc = str(ent.get("location") or "")
        if npc_loc != self.position:
            return _err(
                f"{name}不在{self.view.loc_name(self.position)}，现在在{self.view.loc_name(npc_loc)}。",
                f"先 go {self.view.loc_name(npc_loc)} 再说话。",
            )

        intent = {
            "actor": PLAYER_ID,
            "verb": "talk",
            "args": {"target": npc_id, "message": message, "from": "terminal"},
            "tick": self.view.tick,
            "source": "terminal:proposal",
        }
        try:
            result = self.view.propose(intent)
        except Exception as exc:
            reason = str(getattr(exc, "reason", "") or exc)
            return _err(f"被拒：{reason}", _REASON_HINTS.get(reason, "提案没有通过网关校验。"))

        intent_id = str(result.get("intent_id") or "")
        index = self.view.outbox_count - 1
        lines = [
            f"已提交：你把话带给了{name}（{self.view.loc_name(npc_loc)}）。",
            f"提案 #{index}（intent_id={intent_id}，tick={self.view.tick}），网关返回 accepted=true。",
            "世界状态未改变（state_unchanged=true）；本客户端不生成 NPC 回应，也不写事件日志。",
            "这条提案只停在本客户端内存 outbox，需要主引擎的 Writer 接纳才会落地。",
        ]
        return self._result(lines, kind="notice")


_HELP_LINES = [
    "  look              看当前地点、在场人物与可见物品",
    "  go <地点>         移动到相邻地点（只改客户端坐标，不动世界）",
    "  who               列出全世界在线人物及所在地点",
    "  status            当前角色状态：钱、物品、目标",
    "  quests            任务板：发布人地点、是否可接、截止与报酬",
    "  talk <人名> <话>  以访客身份提交一条 talk 提案（不写世界）",
    "  help              显示这份帮助",
    "",
    "提示：可以用中文名或英文 id 指定地点/人物；输入错误只会得到一句人话，不会抛栈。",
]

_REASON_HINTS = {
    "bad_intent": "提案缺少角色或动词。",
    "bad_intent_args": "提案参数不是 JSON 对象。",
    "permission_denied:world.propose_intent_requires_propose_intent": "客户端没有提案权限。",
    "unregistered_identity": "客户端身份未在网关登记。",
}
