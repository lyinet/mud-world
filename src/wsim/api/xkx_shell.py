"""xkx 文字壳：把 `look/go/ask/buy/fight/loot/score/inventory` 翻译成引擎动作。

这一层只做“人话”适配：不复制内容、不改世界状态结构、不绕过单写者。
读命令直接取只读快照；写命令仍调用 `WorldSession.perform`。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import yaml

from ..core.clock import TICKS_PER_DAY, TICKS_PER_HOUR

DIRECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "north": ("n", "north", "北", "北边", "往北", "向北"),
    "south": ("s", "south", "南", "南边", "往南", "向南"),
    "east": ("e", "east", "东", "东边", "往东", "向东"),
    "west": ("w", "west", "西", "西边", "往西", "向西"),
    "northeast": ("ne", "northeast", "东北", "东北边"),
    "northwest": ("nw", "northwest", "西北", "西北边"),
    "southeast": ("se", "southeast", "东南", "东南边"),
    "southwest": ("sw", "southwest", "西南", "西南边"),
    "northup": ("northup", "nu", "北上", "北上行"),
    "southdown": ("southdown", "sd", "南下", "南下行"),
    "northdown": ("northdown", "nd", "北下", "北下行"),
    "southup": ("southup", "su", "南上", "南上行"),
    "eastup": ("eastup", "eu", "东上", "东上行"),
    "westup": ("westup", "wu", "西上", "西上行"),
    "eastdown": ("eastdown", "ed", "东下", "东下行"),
    "westdown": ("westdown", "wd", "西下", "西下行"),
    "up": ("u", "up", "上", "往上", "攀上"),
    "down": ("d", "down", "下", "往下", "落下"),
    "out": ("out", "出", "出去", "往外"),
    "enter": ("enter", "进", "进入", "往里"),
}

DIRECTION_LABELS: dict[str, str] = {
    "north": "北",
    "northeast": "东北",
    "east": "东",
    "southeast": "东南",
    "south": "南",
    "southwest": "西南",
    "west": "西",
    "northwest": "西北",
    "northup": "北上行",
    "southdown": "南下行",
    "northdown": "北下行",
    "southup": "南上行",
    "eastup": "东上行",
    "westup": "西上行",
    "eastdown": "东下行",
    "westdown": "西下行",
    "up": "上",
    "down": "下",
    "out": "外",
    "enter": "内",
}

DIRECTION_HOTKEYS: dict[str, str] = {
    "north": "n",
    "northeast": "ne",
    "east": "e",
    "southeast": "se",
    "south": "s",
    "southwest": "sw",
    "west": "w",
    "northwest": "nw",
    "northup": "nu",
    "southdown": "sd",
    "northdown": "nd",
    "southup": "su",
    "eastup": "eu",
    "westup": "wu",
    "eastdown": "ed",
    "westdown": "wd",
    "up": "u",
    "down": "d",
    "out": "out",
    "enter": "enter",
}

# 引擎内部职业是英文；台词兜底里会带出来，统一翻成人话。
ROLE_LABELS: dict[str, str] = {
    "fighter": "武师",
    "scholar": "书生",
    "bonze": "僧人",
    "swordsman": "剑客",
    "taoist": "道人",
    "eunach": "宦官",
    "wanderer": "江湖人",
    "visitor": "访客",
    "merchant": "商人",
    "guard": "护卫",
    "official": "官员",
    "teacher": "先生",
    "doctor": "郎中",
    "baker": "厨子",
    "innkeeper": "掌柜",
    "courier": "镖师",
    "blacksmith": "铁匠",
}


def _humanize(text: str) -> str:
    """把引擎内部职业词替换成中文，避免玩家看到 wanderer 之类的标记。"""
    result = str(text or "")
    for code, label in ROLE_LABELS.items():
        result = re.sub(rf"(?<![A-Za-z]){re.escape(code)}(?![A-Za-z])", label, result)
    return result

# 命令别名：英文为主，中文只做输入便利，不发明新玩法。
VERB_ALIASES: dict[str, str] = {
    "look": "look", "l": "look", "看": "look", "环顾": "look", "查看": "look",
    "go": "go", "走": "go", "去": "go", "前往": "go", "移动": "go",
    "ask": "ask", "问": "ask", "打听": "ask",
    "buy": "buy", "买": "buy", "购买": "buy",
    "sell": "sell", "卖": "sell", "出售": "sell",
    "fight": "fight", "打": "fight", "战斗": "fight", "出手": "fight",
    "loot": "loot", "捡": "loot", "拾取": "loot",
    "score": "score", "状态": "score", "属性": "score",
    "inventory": "inventory", "i": "inventory", "背包": "inventory", "物品": "inventory",
    "help": "help", "帮助": "help", "?": "help",
}

_EXTRA_REASON_TEXT = {
    "target_missing": "没指定要找谁/打谁。",
    "self_target": "不能对自己下手。",
    "actor_missing": "你的角色不在这具身体里。",
    "target_is_player": "不能对别的玩家动手。",
    "actor_dead": "你已经倒下了，先想办法回魂。",
    "target_dead": "对方已经倒下。",
    "player_offline": "你离开太久，已经离线；这次操作没有生效。",
    "player_not_entered": "你还没进入世界。",
    "no_combat_stats": "这具身体还没有气血/攻防；当前内容包没有战斗属性。",
    "unknown_item": "这里没有这种货。",
    "wrong_location": "你不在这个交易地点。",
    "bad_quantity": "数量不对；一次只能买卖 1 到 20 件。",
    "insufficient_money": "你的钱不够。",
    "not_enough_money": "你的钱不够完成这笔买卖。",
    "item_not_available": "这里暂时不卖这件货，或已经售罄。",
    "shop_closed": "这里没有店铺，不能买卖。",
    "not_a_merchant": "这里的店铺不做这件货的买卖。",
    "item_missing": "你身上没有足够的这件货。",
    "not_here": "你不在这个交易地点。",
    "unknown_target": "这里没有这个人。",
    "not_together": "对方不在你当前所在的地点。",
    "bad_message": "要说的话不能为空，也不能超过 200 字。",
    "not_adjacent": "那条路走不过去；只能沿相邻出口逐段走。",
    "unknown_location": "没有这个地点。",
    "unknown_verb": "这个动作还没有接上。",
    "forbidden_verb": "这个动作当前不开放。",
    "not_implemented_for_player": "这个玩法还没对玩家开放。",
}


class XKXShell:
    def __init__(self, session) -> None:
        self.session = session
        self.config = self._load_world_config(getattr(session, "world_config_path", None))
        self.directions: dict[str, dict[str, str]] = self._direction_map(self.config)
        self.closed_exits: dict[str, list[dict[str, Any]]] = self._closed_map(self.config)
        self.descriptions: dict[str, str] = {
            str(k): str(v)
            for k, v in (self.config.get("room_descriptions") or {}).items()
            if str(v).strip()
        }

    # ------------------------------------------------------------- 配置
    @staticmethod
    def _load_world_config(path: Path | None) -> dict[str, Any]:
        if path is None:
            return {}
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _direction_map(config: dict[str, Any]) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        raw = config.get("exit_directions") or {}
        if not isinstance(raw, dict):
            return out
        for loc, rows in raw.items():
            if not isinstance(rows, dict):
                continue
            out[str(loc)] = {
                str(direction): str(target)
                for direction, target in rows.items()
                if str(target).strip()
            }
        return out

    @staticmethod
    def _closed_map(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        raw = config.get("closed_exits") or {}
        if not isinstance(raw, dict):
            return out
        for loc, rows in raw.items():
            items: list[dict[str, Any]] = []
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                items.append(
                    {
                        "direction": str(row.get("direction") or ""),
                        "target": str(row.get("target") or ""),
                        "name": str(row.get("name") or row.get("target") or "未名之地"),
                        "reason": str(row.get("reason") or "这一侧还没开放。"),
                    }
                )
            if items:
                out[str(loc)] = items
        return out

    # ------------------------------------------------------------- 入口
    def execute(self, account: str, payload: dict[str, Any], request_id: str = "") -> dict[str, Any]:
        raw = str(payload.get("command") or payload.get("line") or "").strip()
        verb, rest = self._parse(raw)
        state, err = self._ensure_player(account, payload)
        if err:
            return self._reply(
                account, verb or "enter", False, [("error", err)], None, raw, request_id
            )

        try:
            if verb == "help" or (not verb and not rest):
                lines = self._help_lines()
                return self._reply(account, "help", True, lines, state, raw, request_id)
            if verb == "look":
                return self._reply(
                    account, "look", True, self._room_lines(state), state, raw, request_id
                )
            if verb == "go":
                return self._cmd_go(account, rest, state, raw, request_id)
            if verb == "ask":
                return self._cmd_ask(account, rest, state, raw, request_id)
            if verb == "buy":
                return self._cmd_buy(account, rest, state, raw, request_id)
            if verb == "sell":
                return self._cmd_sell(account, rest, state, raw, request_id)
            if verb == "fight":
                return self._cmd_fight(account, rest, state, raw, request_id)
            if verb == "loot":
                return self._cmd_loot(account, rest, state, raw, request_id)
            if verb == "score":
                return self._reply(
                    account, "score", True, self._score_lines(state), state, raw, request_id
                )
            if verb == "inventory":
                return self._reply(
                    account, "inventory", True, self._inventory_lines(state), state, raw, request_id
                )
            # 裸输入方向/地名：能对上出口就当成“走”，否则明确说不明白。
            if not verb and rest:
                row, err = self._resolve_exit(state, raw)
                if row is not None:
                    return self._cmd_go(account, raw, state, raw, request_id)
                return self._reply(
                    account,
                    "unknown",
                    False,
                    [("error", f"不明白「{raw}」。{err} 输入 help 看可用命令。")],
                    state,
                    raw,
                    request_id,
                )
            return self._reply(
                account,
                verb or "unknown",
                False,
                [("error", f"不明白「{raw}」。输入 help 看可用命令。")],
                state,
                raw,
                request_id,
            )
        except Exception as exc:  # 文字壳不能把 500 抛给玩家
            return self._reply(
                account,
                verb or "unknown",
                False,
                [("error", f"命令没能执行：{exc}")],
                state,
                raw,
                request_id,
            )

    def _parse(self, raw: str) -> tuple[str, str]:
        if not raw:
            return "", ""
        first, _, rest = raw.partition(" ")
        token = first.strip().lower()
        verb = VERB_ALIASES.get(token, "")
        if verb:
            return verb, rest.strip()
        # 不是已知动词时留给 execute 判断：可能是方向或地点名。
        return "", raw.strip()

    # ------------------------------------------------------------- 玩家
    def _ensure_player(
        self, account: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        account = str(account or "").strip().lower()
        if not account:
            return None, "缺少账号；请先填写侠名。"
        state = self.session.player_state(account)
        if state.get("ok"):
            player = state.get("player") or {}
            if bool(player.get("online")) and str(player.get("activity") or "") != "offline":
                return state, ""
            # 长时间没操作会离线；文字壳自动重新进入，不把玩家卡在登录页。
            reason = "player_offline"
        else:
            reason = str(state.get("reject_reason") or "")
        if reason not in ("player_not_entered", "player_offline"):
            return None, self._reason(reason)
        pid = self.session._pid(account)
        existing = self.session.state.entities.get(pid)
        prof = self.session.state.players.get(pid)
        if existing is not None:
            display = existing.name
            archetype = ""
        else:
            display = str(payload.get("display_name") or "").strip() or self._display_name(account)
            archetype = str(payload.get("archetype") or "").strip()
            if not archetype:
                archetype = str(self.config.get("archetype") or "游侠")
            if archetype not in ("游侠", "商人", "捕快"):
                archetype = "游侠"
        resp = self.session.perform(
            account,
            "enter",
            {"display_name": display, "archetype": archetype},
        )
        if not resp.get("ok"):
            # 重名时退回到账号派生的侠名，再试一次；不能把玩家卡在登录页。
            code = str(resp.get("reject_reason") or "")
            if code == "bad_display_name" and existing is None:
                fallback = self._display_name(account, force=True)
                resp = self.session.perform(
                    account, "enter", {"display_name": fallback, "archetype": archetype}
                )
            if not resp.get("ok"):
                return None, self._reason(str(resp.get("reject_reason") or ""))
        state = self.session.player_state(account)
        if not state.get("ok"):
            return None, self._reason(str(state.get("reject_reason") or ""))
        return state, ""

    def _display_name(self, account: str, force: bool = False) -> str:
        base = str(self.config.get("display_name") or "").strip()
        if not force and base and self._name_free(base):
            return base
        cleaned = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", account)
        if cleaned and base:
            candidate = ("游侠" + cleaned[-4:])[:12]
        else:
            candidate = cleaned[:10] or "游侠"
        if len(candidate) < 2:
            candidate = f"侠客{candidate}"
        if self._name_free(candidate):
            return candidate
        for i in range(2, 100):
            suffix = str(i)
            trial = (candidate[: 12 - len(suffix)] + suffix)[:12]
            if self._name_free(trial):
                return trial
        return "无名侠客"[:12]

    def _name_free(self, name: str) -> bool:
        if not (2 <= len(name) <= 12):
            return False
        return all(ent.name != name for ent in self.session.state.entities.values())

    # ------------------------------------------------------------- 出口
    def _canonical_direction(self, text: str) -> str:
        key = str(text or "").strip().lower()
        for direction, aliases in DIRECTION_ALIASES.items():
            if key in aliases:
                return direction
        return ""

    def _content_location(self, loc_id: str) -> dict[str, Any]:
        return (self.session.content.locations or {}).get(str(loc_id)) or {}

    def _direction_for(self, loc_id: str, target: str) -> str:
        for row in self._content_location(loc_id).get("exits_detail") or []:
            if str(row.get("to") or "") == str(target):
                return str(row.get("dir") or "")
        for direction, dest in (self.directions.get(loc_id) or {}).items():
            if dest == target:
                return direction
        return ""

    @staticmethod
    def _row(exit_row: dict[str, Any]) -> dict[str, Any]:
        direction = str(exit_row.get("dir") or "")
        available = bool(exit_row.get("available"))
        reason = str(exit_row.get("reason") or "")
        if not available and not reason:
            reason = "这一侧还没开放。"
        return {
            "id": str(exit_row.get("to") or ""),
            "name": str(exit_row.get("name") or exit_row.get("to") or "未名之地"),
            "direction": direction,
            "direction_label": DIRECTION_LABELS.get(direction, "路"),
            "hotkey": DIRECTION_HOTKEYS.get(direction, ""),
            "available": available,
            "reason": reason,
        }

    def _exit_rows(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        player = state.get("player") or {}
        loc_id = str(player.get("location") or "")
        location = state.get("location") or {}
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()

        # 内容包自带方向/状态的出口优先：不可走的出口也保留，并写明原因。
        detail = self._content_location(loc_id).get("exits_detail") or []
        for exit_row in detail:
            row = self._row(exit_row)
            key = row["id"] or f"exit:{row['direction']}:{row['name']}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

        # 没有内容方向表时退回引擎地点视图 + 世界配置方向表（旧九地点世界）。
        if not detail:
            for item in location.get("exits") or []:
                target = str(item.get("id") or "")
                if not target:
                    continue
                seen.add(target)
                direction = self._direction_for(loc_id, target)
                exists = bool(item.get("exists", True))
                rows.append(
                    {
                        "id": target,
                        "name": str(item.get("name") or self._loc_name(target)),
                        "direction": direction,
                        "direction_label": DIRECTION_LABELS.get(direction, "路"),
                        "hotkey": DIRECTION_HOTKEYS.get(direction, ""),
                        "available": exists,
                        "reason": "" if exists else "目标地点不在当前世界（内容边界）。",
                    }
                )

        for item in self.closed_exits.get(loc_id) or []:
            target = str(item.get("target") or "")
            key = target or f"closed:{item.get('direction')}:{item.get('name')}"
            if key in seen:
                continue
            seen.add(key)
            direction = str(item.get("direction") or "")
            rows.append(
                {
                    "id": target,
                    "name": str(item.get("name") or self._loc_name(target)),
                    "direction": direction,
                    "direction_label": DIRECTION_LABELS.get(direction, "路"),
                    "hotkey": DIRECTION_HOTKEYS.get(direction, ""),
                    "available": False,
                    "reason": str(item.get("reason") or "这一侧还没开放。"),
                }
            )
        order = list(DIRECTION_LABELS)
        return sorted(
            rows,
            key=lambda row: (
                order.index(row["direction"]) if row["direction"] in order else len(order),
                str(row["name"]),
            ),
        )

    def _resolve_exit(
        self, state: dict[str, Any], text: str
    ) -> tuple[dict[str, Any] | None, str]:
        rows = self._exit_rows(state)
        raw = str(text or "").strip()
        if not raw:
            return None, "你要往哪里走？" + self._exit_hint(rows)
        direction = self._canonical_direction(raw)
        if direction:
            for row in rows:
                if row["direction"] == direction:
                    return row, ""
        key = raw.lower()
        for row in rows:
            if key == str(row["id"]).lower() or key == str(row["name"]).lower():
                return row, ""
        # 中文地名允许前缀/包含匹配，例如「铁匠」→「铁匠铺」。
        for row in rows:
            if key and (key in str(row["name"]).lower() or key in str(row["id"]).lower()):
                return row, ""
        return None, f"这里没有叫「{raw}」的出口。" + self._exit_hint(rows)

    def _exit_hint(self, rows: list[dict[str, Any]]) -> str:
        usable = [r for r in rows if r["available"]]
        blocked = [r for r in rows if not r["available"]]
        parts = []
        if usable:
            parts.append(
                "可用出口："
                + "、".join(
                    f"{r['direction_label']}→{r['name']}"
                    if r["direction"]
                    else f"{r['name']}"
                    for r in usable
                )
            )
        if blocked:
            parts.append(
                "未开放："
                + "、".join(
                    f"{r['direction_label']}→{r['name']}（{r['reason']}）"
                    if r["direction"]
                    else f"{r['name']}（{r['reason']}）"
                    for r in blocked
                )
            )
        return "；".join(parts) + "。" if parts else "这里没有出口。"

    # ------------------------------------------------------------- 命令
    def _cmd_go(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        row, err = self._resolve_exit(state, rest)
        if err or row is None:
            return self._reply(account, "go", False, [("error", err)], state, raw, request_id)
        if not row["available"]:
            where = f"往{row['direction_label']}的" if row["direction"] else ""
            text = f"{where}{row['name']}走不过去：{row['reason']}"
            return self._reply(account, "go", False, [("error", text)], state, raw, request_id)
        resp = self.session.perform(account, "move", {"to": row["id"]})
        if not resp.get("ok"):
            text = self._reason(str(resp.get("reject_reason") or ""), fallback="走不过去。")
            return self._reply(account, "go", False, [("error", text)], state, raw, request_id)
        after = self._state(account)
        if after is None:
            return self._reply(account, "go", True, [("info", f"你来到了{row['name']}。")], state, raw, request_id)
        where = "往" + row["direction_label"] if row["direction"] else "沿路"
        lines: list[tuple[str, str]] = [(("info"), f"你{where}走，来到{row['name']}。")]
        lines.extend(self._room_lines(after))
        return self._reply(account, "go", True, lines, after, raw, request_id)

    def _cmd_ask(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        npcs = list(state.get("present_npcs") or [])
        if not npcs:
            return self._reply(
                account, "ask", False,
                [("error", "这里没有可以搭话的人。")], state, raw, request_id,
            )
        target, message = self._match_npc_prefix(npcs, rest)
        if target is None:
            label = self._first_word(rest)
            return self._reply(
                account, "ask", False,
                [("error", f"这里没有「{label or '谁'}」。在场的有：" + "、".join(n["name"] for n in npcs) + "。")],
                state, raw, request_id,
            )
        message = message or "你好，请问这里有什么可做的？"
        resp = self.session.perform(
            account, "talk", {"target": target["id"], "message": message}
        )
        self.session.finalize_dialogue(resp, {"target": target["id"], "message": message})
        if not resp.get("ok"):
            return self._reply(
                account, "ask", False,
                [("error", self._reason(str(resp.get("reject_reason") or ""), fallback="对方没有理你。"))],
                state, raw, request_id,
            )
        reply = _humanize(str((resp.get("details") or {}).get("reply") or "").strip())
        lines = [
            ("cmd", f"你对{target['name']}说：{message}"),
            ("npc", f"{target['name']}答：{reply or '（对方只是点了点头，没有多话。）'}"),
        ]
        after = self._state(account) or state
        return self._reply(account, "ask", True, lines, after, raw, request_id)

    def _match_npc_prefix(
        self, npcs: Iterable[dict[str, Any]], text: str
    ) -> tuple[dict[str, Any] | None, str]:
        raw = str(text or "").strip()
        if not raw:
            return None, ""
        # 先按名字/ID 最长前缀匹配，剩余部分当台词，兼容「问 店小二 有酒吗」。
        best: tuple[int, dict[str, Any] | None, str] = (0, None, "")
        for npc in npcs:
            for key in (str(npc.get("name") or ""), str(npc.get("id") or "")):
                if key and raw.lower().startswith(key.lower()) and len(key) > best[0]:
                    best = (len(key), npc, raw[len(key):].strip())
        if best[1] is not None:
            return best[1], best[2]
        # 再退到包含匹配，整段都当对象名，台词用默认。
        for npc in npcs:
            name = str(npc.get("name") or "")
            if name and name in raw:
                return npc, ""
        return None, ""

    def _cmd_buy(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        goods = list((state.get("shop") or {}).get("buy") or [])
        if not goods:
            return self._reply(
                account, "buy", False,
                [("error", "这里没有店铺，买不到东西。")], state, raw, request_id,
            )
        row, remainder = self._match_goods_prefix(goods, rest)
        if row is None:
            label = self._goods_label(rest) or "这件货"
            return self._reply(
                account, "buy", False,
                [("error", "这里不卖「" + label + "」。货架上有：" + self._goods_hint(goods) + "。")],
                state, raw, request_id,
            )
        qty = self._qty_from(remainder)
        if not row.get("available"):
            reason = str(row.get("unavailable_reason") or "")
            text = "这件货已经售罄。" if reason == "sold_out" else "这件货现在买不到。"
            return self._reply(account, "buy", False, [("error", text)], state, raw, request_id)
        resp = self.session.perform(
            account, "buy", {"item": row["item"], "qty": qty}
        )
        if not resp.get("ok"):
            code = str(resp.get("reject_reason") or "")
            return self._reply(
                account, "buy", False,
                [("error", self._reason(code, fallback="这单买卖没有做成。"))],
                state, raw, request_id,
            )
        after = self._state(account) or state
        before_money = int((state.get("player") or {}).get("wallet", {}).get("money", 0))
        after_money = int((after.get("player") or {}).get("wallet", {}).get("money", 0))
        spent = max(0, before_money - after_money)
        lines = [
            ("info", f"你买下{row['name']}×{qty}，付出{spent}文（含税）。余额{after_money}文。")
        ]
        return self._reply(account, "buy", True, lines, after, raw, request_id)

    def _cmd_sell(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        rows = list((state.get("shop") or {}).get("sell") or [])
        if not rows:
            return self._reply(
                account, "sell", False,
                [("error", "你身上没有可卖的东西。")], state, raw, request_id,
            )
        row, remainder = self._match_goods_prefix(rows, rest)
        if row is None:
            label = self._goods_label(rest) or "这件货"
            return self._reply(
                account, "sell", False,
                [("error", "你身上没有「" + label + "」。可卖：" + "、".join(r["name"] for r in rows) + "。")],
                state, raw, request_id,
            )
        qty = self._qty_from(remainder)
        if int(row.get("qty", 0)) < qty:
            return self._reply(
                account, "sell", False,
                [("error", f"你只有{row['name']}×{row.get('qty', 0)}，卖不了{qty}件。")],
                state, raw, request_id,
            )
        resp = self.session.perform(
            account, "sell", {"item": row["item"], "qty": qty}
        )
        if not resp.get("ok"):
            return self._reply(
                account, "sell", False,
                [("error", self._reason(str(resp.get("reject_reason") or ""), fallback="这单买卖没有做成。"))],
                state, raw, request_id,
            )
        after = self._state(account) or state
        before_money = int((state.get("player") or {}).get("wallet", {}).get("money", 0))
        after_money = int((after.get("player") or {}).get("wallet", {}).get("money", 0))
        earned = max(0, after_money - before_money)
        lines = [
            ("info", f"你卖出{row['name']}×{qty}，到手{earned}文。余额{after_money}文。")
        ]
        return self._reply(account, "sell", True, lines, after, raw, request_id)

    def _cmd_fight(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        reason = self._combat_reason("fight")
        if reason:
            return self._reply(
                account, "fight", False,
                [("warn", f"暂不可用：{reason}")],
                state, raw, request_id,
            )
        npcs = list(state.get("present_npcs") or [])
        target, _ = self._match_npc_prefix(npcs, rest)
        if target is None:
            hint = "、".join(n["name"] for n in npcs) if npcs else "（这里没有别人）"
            label = self._first_word(rest)
            return self._reply(
                account, "fight", False,
                [("error", f"这里没有可打的「{label or '人'}」。在场：{hint}。")],
                state, raw, request_id,
            )
        resp = self.session.perform(
            account,
            "fight",
            {"target": target["id"], "opponent": target["id"], "npc": target["id"]},
        )
        if not resp.get("ok"):
            return self._reply(
                account, "fight", False,
                [("error", self._reason(str(resp.get("reject_reason") or ""), fallback="这一场没能打成。"))],
                state, raw, request_id,
            )
        after = self._state(account) or state
        lines = [("combat", f"你向{target['name']}出手。")]
        lines.extend(self._detail_lines(resp.get("details") or {}))
        lines.extend(self._score_core_lines(after))
        return self._reply(account, "fight", True, lines, after, raw, request_id)

    def _cmd_loot(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        reason = self._combat_reason("loot")
        if reason:
            return self._reply(
                account, "loot", False,
                [("warn", f"暂不可用：{reason}")],
                state, raw, request_id,
            )
        npcs = list(state.get("present_npcs") or [])
        target, _ = self._match_npc_prefix(npcs, rest) if rest else (None, "")
        payload: dict[str, Any] = {}
        if target is not None:
            payload = {"target": target["id"], "npc": target["id"]}
        resp = self.session.perform(account, "loot", payload)
        if not resp.get("ok"):
            return self._reply(
                account, "loot", False,
                [("error", self._reason(str(resp.get("reject_reason") or ""), fallback="没有捡到东西。"))],
                state, raw, request_id,
            )
        after = self._state(account) or state
        lines = [("info", "你查看地上，把能带的都收了起来。")]
        lines.extend(self._detail_lines(resp.get("details") or {}))
        return self._reply(account, "loot", True, lines, after, raw, request_id)

    def _combat_reason(self, verb: str) -> str:
        """返回“为什么现在不能打/不能捡”的人话；可用时返回空串。"""
        try:
            registered = self.session.registry.get(verb) is not None
        except Exception:
            registered = False
        if not registered:
            if verb == "fight":
                return "战斗动作还没接上（actions 层尚未注册 fight）。现在可以走、看、问、买卖。"
            return "捡拾动作还没接上（actions 层尚未注册 loot）。"
        if verb == "fight":
            # 动作注册不等于能打：玩家还没有气血/攻防时，点了也不会结算。
            try:
                for ent in self.session.state.entities.values():
                    if str(getattr(ent, "id", "")).startswith("player:") and int(
                        getattr(ent, "max_hp", 0) or 0
                    ) > 0:
                        return ""
            except Exception:
                return ""
            return "战斗动作已注册，但当前角色还没有气血/攻防；等引擎把内容属性接到玩家后再开打。"
        return ""

    def _combat_ready(self, verb: str) -> bool:
        return self._combat_reason(verb) == ""

    # ------------------------------------------------------------- 渲染
    def _room_lines(self, state: dict[str, Any]) -> list[tuple[str, str]]:
        location = state.get("location") or {}
        loc_id = str(location.get("id") or "")
        name = str(location.get("name") or loc_id or "未知地点")
        description = (
            str(location.get("description") or "").strip()
            or self.descriptions.get(loc_id, "")
        )
        # xkx 的 desc 是 LPC 源码折行，中文直接拼接才不会在句中留下断行。
        description = re.sub(r"\s*\n\s*", "", description)
        if not description:
            description = f"你站在{name}。四周的景物安静下来，像是在等你先开口。"
        exits = self._exit_rows(state)
        lines: list[tuple[str, str]] = [("room", f"【{name}】{description}")]
        usable = [r for r in exits if r["available"]]
        if usable:
            lines.append(
                (
                    "info",
                    "出口："
                    + "、".join(
                        f"{r['direction_label']}→{r['name']}" if r["direction"] else r["name"]
                        for r in usable
                    )
                    + "。",
                )
            )
        blocked = [r for r in exits if not r["available"]]
        if blocked:
            lines.append(
                (
                    "warn",
                    "未开放出口："
                    + "、".join(
                        (
                            f"{r['direction_label']}→{r['name']}（{r['reason']}）"
                            if r["direction"]
                            else f"{r['name']}（{r['reason']}）"
                        )
                        for r in blocked
                    )
                    + "。",
                )
            )
        if not exits:
            lines.append(("warn", "这里没有出口。"))
        npcs = list(state.get("present_npcs") or [])
        if npcs:
            lines.append(("npc", "在场人物：" + "、".join(str(n["name"]) for n in npcs) + "。"))
        ground = self._ground_items(loc_id)
        if ground:
            lines.append(("info", "地上散落：" + "、".join(ground) + "。"))
        goods = [g for g in ((state.get("shop") or {}).get("buy") or []) if g.get("available")]
        if goods:
            lines.append(
                (
                    "shop",
                    "货架："
                    + "、".join(f"{row['name']} {row['total']}文（存{row['stock']}）" for row in goods)
                    + "。",
                )
            )
        money = int((state.get("player") or {}).get("wallet", {}).get("money", 0))
        lines.append(("info", f"你身上有{money}文。"))
        return lines

    def _ground_items(self, loc_id: str) -> list[str]:
        """房间里内容包摆放的地面物件（物品），按真实数量展示，不重复计数。"""
        parts: list[str] = []
        for obj in self._content_location(loc_id).get("objects") or []:
            if str(obj.get("kind") or "") != "item":
                continue
            ref = str(obj.get("ref") or "")
            row = (self.session.content.items or {}).get(ref) or {}
            name = str(row.get("name") or ref.rstrip("/").rsplit("/", 1)[-1] or ref)
            count = int(obj.get("count") or 0)
            parts.append(f"{name}×{count}" if count > 1 else name)
        return parts

    def _score_lines(self, state: dict[str, Any]) -> list[tuple[str, str]]:
        player = state.get("player") or {}
        lines = [
            ("room", f"【{player.get('name', '无名')}】{player.get('archetype', '')} / {player.get('role', '')}"),
            ("info", f"所在：{player.get('location_name', '未知')}"),
        ]
        lines.extend(self._score_core_lines(state))
        sect = state.get("sect") or {}
        sect_name = str(sect.get("name") or sect.get("sect_name") or "")
        if sect_name and sect_name != "无":
            lines.append(("info", f"门派：{sect_name}（{sect.get('rank') or '门人'}）"))
        else:
            lines.append(("info", "门派：尚未拜入门派。"))
        skills = list(player.get("skills_detail") or [])
        if skills:
            lines.append(
                (
                    "info",
                    "技能："
                    + "、".join(f"{s['name']} {s['level']}级" for s in skills),
                )
            )
        return lines

    def _score_core_lines(self, state: dict[str, Any]) -> list[tuple[str, str]]:
        player = state.get("player") or {}
        ent = self._entity(state)
        stamina = float(player.get("stamina") or 0)
        max_stamina = float(player.get("max_stamina") or 0)
        lines: list[tuple[str, str]] = [
            ("info", f"体力：{stamina:g}/{max_stamina:g}")
        ]
        if ent is not None and int(getattr(ent, "max_hp", 0) or 0) > 0:
            hp = int(getattr(ent, "hp", 0) or 0)
            max_hp = int(getattr(ent, "max_hp", 0) or 0)
            lines.append(("info", f"气血：{hp}/{max_hp}"))
            attack = int(getattr(ent, "attack", 0) or 0)
            defense = int(getattr(ent, "defense", 0) or 0)
            if attack or defense:
                lines.append(("info", f"攻防：攻{attack} / 防{defense}"))
        else:
            lines.append(("warn", "气血：尚未开放（战斗属性还没接入当前角色）。"))
        money = int((player.get("wallet") or {}).get("money", 0))
        lines.append(("info", f"银两：{money}文"))
        tick = int(state.get("tick") or 0)
        day = tick // TICKS_PER_DAY + 1
        hour = (tick % TICKS_PER_DAY) // TICKS_PER_HOUR
        lines.append(("info", f"世界：第{day}天 {hour:02d}时"))
        return lines

    def _inventory_lines(self, state: dict[str, Any]) -> list[tuple[str, str]]:
        player = state.get("player") or {}
        wallet = player.get("wallet") or {}
        items = list(wallet.get("items") or [])
        lines: list[tuple[str, str]] = [("room", "【行囊】")]
        if not items:
            lines.append(("info", "你身上空空如也。"))
        else:
            for row in items:
                lines.append(
                    ("info", f"{row['name']} ×{row['qty']}（折卖 {row.get('sell_price', 0)}文/件）")
                )
        lines.append(("info", f"银两：{int(wallet.get('money', 0))}文"))
        equipment = list((player.get("equipment") or {}).get("slots") or [])
        worn = [s for s in equipment if not s.get("empty")]
        if worn:
            lines.append(
                ("info", "已装备：" + "、".join(f"{s['slot_name']}={s['name']}" for s in worn))
            )
        return lines

    def _help_lines(self) -> list[tuple[str, str]]:
        fight_reason = self._combat_reason("fight")
        loot_reason = self._combat_reason("loot")
        fight_text = "可用" if not fight_reason else f"暂不可用（{fight_reason}）"
        loot_text = "可用" if not loot_reason else f"暂不可用（{loot_reason}）"
        stats = getattr(self.session.content, "world_stats", {}) or {}
        if stats:
            world_text = (
                f"世界：{stats.get('rooms_in_slice', 0)} 间房 / "
                f"{stats.get('npcs_in_slice', 0)} 位 NPC / "
                f"{stats.get('exits_walkable', 0)} 条可走出口 / "
                f"{stats.get('exits_blocked', 0)} 条未开放出口。"
            )
        else:
            world_text = ""
        lines = [
            ("room", "【侠客行 · 命令】"),
            ("info", "look（看）        环顾当前地点。"),
            ("info", "go <方向|地点>    走。方向可打 n/s/e/w/ne/nw/se/sw/up/down，也可点地图热区。"),
            ("info", "ask <人> <话>     向在场 NPC 搭话。"),
            ("info", "buy <货> [数量]   在当前店铺买货。"),
            ("info", "sell <货> [数量]  把身上的货卖给当前店铺。"),
            ("info", f"fight <人>        动手。{fight_text}。"),
            ("info", f"loot [人]         捡拾。{loot_text}。"),
            ("info", "score（状态）     看自己的体力/银两/技能。"),
            ("info", "inventory（背包） 看行囊与装备。"),
            ("info", "help（帮助）      再看这张表。"),
        ]
        if world_text:
            lines.append(("info", world_text))
        return lines

    def _detail_lines(self, details: dict[str, Any]) -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = []
        for key in ("lines", "log", "rounds", "events", "drops", "items"):
            value = details.get(key)
            if isinstance(value, list):
                for item in value:
                    text = self._value_text(item)
                    if text:
                        lines.append(("combat", text))
        for key in ("text", "message", "result", "summary", "reply"):
            text = self._value_text(details.get(key))
            if text:
                lines.append(("combat", text))
        return lines

    @staticmethod
    def _value_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, dict):
            for key in ("text", "message", "log", "result", "summary", "name", "item"):
                text = XKXShell._value_text(value.get(key))
                if text:
                    return text
            return ""
        return ""

    # ------------------------------------------------------------- 工具
    def _state(self, account: str) -> dict[str, Any] | None:
        state = self.session.player_state(account)
        return state if state.get("ok") else None

    def _entity(self, state: dict[str, Any]):
        player = state.get("player") or {}
        pid = str(player.get("entity_id") or "")
        return self.session.state.entities.get(pid) if pid else None

    def _loc_name(self, loc_id: str) -> str:
        row = (self.session.content.locations or {}).get(str(loc_id)) or {}
        return str(row.get("name") or loc_id or "未知地点")

    def _reason(self, code: str, fallback: str = "") -> str:
        code = str(code or "")
        if code in _EXTRA_REASON_TEXT:
            return _EXTRA_REASON_TEXT[code]
        try:
            text = self.session._reason_text(code)
        except Exception:
            text = ""
        return text or fallback or f"操作没有成功（{code or '未知原因'}）。"

    @staticmethod
    def _first_word(text: str) -> str:
        parts = str(text or "").strip().split()
        return parts[0] if parts else ""

    @staticmethod
    def _goods_label(text: str) -> str:
        return re.sub(r"\s*\d+\s*$", "", str(text or "").strip())

    @staticmethod
    def _qty_from(text: str) -> int:
        raw = str(text or "").strip()
        if not raw:
            return 1
        match = re.fullmatch(r"\d+", raw)
        if not match:
            return 1
        return max(1, min(20, int(raw)))

    @staticmethod
    def _match_goods_prefix(
        rows: Iterable[dict[str, Any]], text: str
    ) -> tuple[dict[str, Any] | None, str]:
        raw = str(text or "").strip()
        if not raw:
            return None, ""
        best: tuple[int, dict[str, Any] | None, str] = (0, None, "")
        for row in rows:
            for key in (str(row.get("name") or ""), str(row.get("item") or "")):
                if key and raw.lower().startswith(key.lower()) and len(key) > best[0]:
                    best = (len(key), row, raw[len(key):].strip())
        if best[1] is not None:
            return best[1], best[2]
        for row in rows:
            name = str(row.get("name") or "")
            if name and name in raw:
                return row, ""
        return None, ""

    @staticmethod
    def _goods_hint(goods: Iterable[dict[str, Any]]) -> str:
        rows = []
        for row in goods:
            tag = "" if row.get("available") else "（售罄）"
            rows.append(f"{row['name']}{tag}")
        return "、".join(rows) if rows else "（空）"

    def _reply(
        self,
        account: str,
        verb: str,
        ok: bool,
        lines: Iterable[tuple[str, str]],
        state: dict[str, Any] | None,
        raw: str,
        request_id: str,
    ) -> dict[str, Any]:
        payload_lines = [{"kind": kind, "text": text} for kind, text in lines]
        return {
            "ok": bool(ok),
            "profile_version": 1,
            "account_id": account,
            "command": raw,
            "verb": verb,
            "request_id": request_id,
            "tick": int((state or {}).get("tick") or 0),
            "lines": payload_lines,
            "view": self._view(state) if state else None,
            "combat_ready": {
                "fight": self._combat_ready("fight"),
                "loot": self._combat_ready("loot"),
            },
            "combat_reason": {
                "fight": self._combat_reason("fight"),
                "loot": self._combat_reason("loot"),
            },
            "world": dict(getattr(self.session.content, "world_stats", {}) or {}),
        }

    def _view(self, state: dict[str, Any]) -> dict[str, Any]:
        player = state.get("player") or {}
        wallet = player.get("wallet") or {}
        npcs = [
            {
                "id": n.get("id"),
                "name": n.get("name"),
                "role": n.get("role"),
                "role_label": ROLE_LABELS.get(str(n.get("role") or ""), str(n.get("role") or "江湖人")),
                "skills": n.get("skills"),
            }
            for n in (state.get("present_npcs") or [])
        ]
        goods = list((state.get("shop") or {}).get("buy") or [])
        sell = list((state.get("shop") or {}).get("sell") or [])
        return {
            "tick": int(state.get("tick") or 0),
            "player": {
                "name": player.get("name"),
                "archetype": player.get("archetype"),
                "role": player.get("role"),
                "location": player.get("location"),
                "location_name": player.get("location_name"),
                "stamina": player.get("stamina"),
                "max_stamina": player.get("max_stamina"),
                "money": int(wallet.get("money", 0)),
                "online": player.get("online"),
                "activity": player.get("activity"),
                "skills": player.get("skills_detail") or [],
                "equipment": player.get("equipment") or {},
            },
            "room": {
                "id": (state.get("location") or {}).get("id"),
                "name": (state.get("location") or {}).get("name"),
                "description": (
                    str((state.get("location") or {}).get("description") or "").strip()
                    or self.descriptions.get(str((state.get("location") or {}).get("id") or ""), "")
                ),
            },
            "exits": self._exit_rows(state),
            "npcs": npcs,
            "goods": [
                {
                    "item": row.get("item"),
                    "name": row.get("name"),
                    "price": row.get("price"),
                    "total": row.get("total"),
                    "stock": row.get("stock"),
                    "available": row.get("available"),
                    "unavailable_reason": row.get("unavailable_reason"),
                }
                for row in goods
            ],
            "sell": [
                {
                    "item": row.get("item"),
                    "name": row.get("name"),
                    "qty": row.get("qty"),
                    "sell_price": row.get("sell_price"),
                }
                for row in sell
            ],
            "inventory": list(wallet.get("items") or []),
            "combat_ready": {
                "fight": self._combat_ready("fight"),
                "loot": self._combat_ready("loot"),
            },
            "combat_reason": {
                "fight": self._combat_reason("fight"),
                "loot": self._combat_reason("loot"),
            },
            "world": dict(getattr(self.session.content, "world_stats", {}) or {}),
        }
