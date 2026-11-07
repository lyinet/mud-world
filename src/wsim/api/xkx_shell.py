"""xkx 文字壳：把 `look/go/ask/buy/fight/loot/score/inventory` 翻译成引擎动作。

这一层只做“人话”适配：不复制内容、不改世界状态结构、不绕过单写者。
读命令直接取只读快照；写命令仍调用 `WorldSession.perform`。
"""
from __future__ import annotations

import difflib
import json
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
    "get": "loot", "拿": "loot", "取": "loot",
    "wear": "wear", "穿": "wear", "穿上": "wear", "穿戴": "wear",
    "wield": "wield", "持": "wield", "持起": "wield", "握": "wield",
    "remove": "remove", "脱": "remove", "脱下": "remove",
    "unwield": "unwield", "放下": "unwield", "收剑": "unwield",
    "drop": "drop", "丢": "drop", "丢下": "drop", "扔掉": "drop",
    "rest": "rest", "休息": "rest",
    "sleep": "sleep", "睡": "sleep", "睡觉": "sleep",
    "give": "give", "交给": "give", "给": "give", "交付": "give",
    "quests": "quests", "任务": "quests", "支线": "quests",
    "score": "score", "状态": "score", "属性": "score",
    "inventory": "inventory", "i": "inventory", "背包": "inventory", "物品": "inventory",
    "help": "help", "帮助": "help", "?": "help",
    "bai": "bai", "拜师": "bai", "拜": "bai", "投师": "bai",
    "learn": "learn", "学": "learn", "学习": "learn", "请教": "learn",
    "practice": "practice", "练": "practice", "练习": "practice", "修炼": "practice",
    "climb": "climb", "爬": "climb", "攀": "climb", "爬树": "climb",
    "jump": "jump", "跳": "jump", "跃": "jump", "跳木桩": "jump",
    "revive": "revive", "还阳": "revive", "复活": "revive", "回魂": "revive",
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
    "loot_not_found": "地上没有这件东西（或者已经被人捡走了）。",
    "target_required": "没指定要把东西交给谁。",
    "target_not_found": "这里没有这个人。",
    "quest_item_required": "没指定要交出的物品。",
    "quest_not_found": "对方不收这件东西，或者这不是对应的任务物品。",
    "quest_not_accepted": "这条支线还没接：先去问过委托人。",
    "quest_already_completed": "这条支线已经完成了。",
    "quest_item_missing": "你身上没有这件任务物品。",
    "bad_qty": "数量不对；捡东西至少要 1 件。",
    "player_dead": "你已经倒下了，先想办法回魂。",
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
    "master_not_here": "这里没有你说的这位师父。",
    "master_ambiguous": "这里有不止一个人符合这个称呼，请说清楚。",
    "master_dead": "这位师父已经不在人世。",
    "not_a_master": "对方不是可以拜师的人物。",
    "already_apprentice": "你已经是这位师父的弟子了。",
    "already_has_master": "你已经另有师父；原版不允许随意改投。",
    "already_in_sect": "你已经身在门派之中。",
    "in_ghost_gate": "你身处鬼门关，做不得阳间的事。",
    "unknown_skill": "没有这门技能。",
    "not_apprentice_of_teacher": "你与这位师父没有师承关系，他不能教你。",
    "teacher_lacks_skill": "这位师父不会这门技能。",
    "skill_not_lower": "你的这门技能已经不低于师父，学不到新东西了。",
    "master_wont_teach": "技能已到 250 级，师父无法再教。",
    "insufficient_combat_exp": "你的实战经验不足以驾驭这门技能。",
    "insufficient_potential": "潜能不足，付不起这次学习。",
    "insufficient_jing": "精力不足，支撑不了这么多次学习。",
    "skill_not_learned": "你还没有学会这门技能，无法自行练习。",
    "basic_skill_missing": "你还没有对应的基本功，无法练习这门技能。",
    "practice_ratio_low": "基本功相对这门技能还太浅，练不上去。",
    "insufficient_stamina": "体力不足 30，练不动了。",
    "climb_wrong_place": "这里没有可以攀爬的树。",
    "climb_target_unknown": "原版这里只能「climb tree」。",
    "dodge_too_low": "你的轻功还不到 50 级，爬不上去。",
    "jump_wrong_place": "这里不是跳木桩的地方。",
    "jump_target_unknown": "你要跳向什么？原版这里只有木桩。",
    "not_dead": "你还活着，不必还阳。",
    "not_in_ghost_gate": "你不在鬼门关，无法还阳。",
    "no_revival_location": "找不到可以还阳的地点。",
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
        # 帮助内容包：153 命令 / 32 主题。只读，不参与任何玩法判定。
        self.help_commands: list[dict[str, Any]] = []
        self.help_topics: list[dict[str, Any]] = []
        self.help_counts: dict[str, Any] = {}
        self.help_error = ""
        self._load_help()

    # ------------------------------------------------------------- 帮助数据
    def _load_help(self) -> None:
        pack_root = Path(getattr(self.session, "content_root", Path("content")))
        candidates = [
            pack_root.parent / "xkx_help",
            pack_root / "xkx_help",
            Path(getattr(self.session, "root", pack_root.parent)) / "content" / "xkx_help",
        ]
        base = next((path for path in candidates if path.exists()), None)
        if base is None:
            self.help_error = "帮助内容包不存在（content/xkx_help）。"
            return
        try:
            commands_doc = yaml.safe_load(
                (base / "commands.yaml").read_text(encoding="utf-8")
            ) or {}
            topics_doc = yaml.safe_load((base / "topics.yaml").read_text(encoding="utf-8")) or {}
            manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
        except Exception as exc:
            self.help_error = f"帮助内容包读取失败：{exc}"
            return
        rows = commands_doc.get("commands") if isinstance(commands_doc, dict) else None
        topics = topics_doc.get("topics") if isinstance(topics_doc, dict) else None
        self.help_commands = [dict(row) for row in rows or [] if isinstance(row, dict)]
        self.help_topics = [dict(row) for row in topics or [] if isinstance(row, dict)]
        counts = manifest.get("counts") if isinstance(manifest, dict) else None
        self.help_counts = dict(counts) if isinstance(counts, dict) else {}
        if not self.help_commands or not self.help_topics:
            self.help_error = "帮助内容包为空（commands.yaml/topics.yaml 没有条目）。"

    def _shell_entry(self, row: dict[str, Any]) -> str:
        """只有命令名本身能被本文字壳接受才算“已接入口”。

        别名可能与其他命令重名（例如 hit 的别名「出手」恰好被 fight 接走），
        不能因此把原命令说成可用。
        """
        name = str(row.get("name") or "").strip()
        if name and name.lower() in VERB_ALIASES:
            return name
        return ""

    def _find_help_command(self, token: str) -> dict[str, Any] | None:
        key = str(token or "").strip().lower()
        if not key:
            return None
        for row in self.help_commands:
            if str(row.get("name") or "").lower() == key:
                return row
            for alias in row.get("aliases") or []:
                if str(alias).strip().lower() == key:
                    return row
        return None

    def _find_help_topic(self, text: str) -> dict[str, Any] | None:
        key = str(text or "").strip()
        if not key:
            return None
        for row in self.help_topics:
            if str(row.get("id") or "") == key:
                return row
            title = str(row.get("title") or "")
            if title == key or (title and (title in key or key in title)):
                return row
        return None

    @staticmethod
    def _status_text(row: dict[str, Any]) -> str:
        status = str(row.get("status") or "未注明")
        if status == "已实现":
            return "已实现"
        reason = str(row.get("blocker") or "").strip() or "原因未注明"
        return f"{status}（{reason}）"

    def catalog(self) -> dict[str, Any]:
        """给 `/api/xkx/help` 与前端补全用的只读目录。"""
        commands = []
        for row in self.help_commands:
            entry = self._shell_entry(row)
            commands.append(
                {
                    "name": str(row.get("name") or ""),
                    "aliases": [str(a) for a in (row.get("aliases") or [])],
                    "params": str(row.get("params") or "—"),
                    "summary": str(row.get("summary") or ""),
                    "system": str(row.get("system") or ""),
                    "status": str(row.get("status") or ""),
                    "blocker": str(row.get("blocker") or ""),
                    "evidence": str(row.get("evidence") or ""),
                    "shell": bool(entry),
                    "shell_verb": VERB_ALIASES.get(entry, "") if entry else "",
                }
            )
        topics = [
            {
                "id": str(row.get("id") or ""),
                "title": str(row.get("title") or ""),
                "category": str(row.get("category") or ""),
                "status": str(row.get("status") or ""),
            }
            for row in self.help_topics
        ]
        return {
            "ok": not self.help_error,
            "error": self.help_error,
            "counts": self.help_counts,
            "commands": commands,
            "topics": topics,
            "shell_verbs": sorted(set(VERB_ALIASES.values())),
            # 文字壳真正接受的输入串（含中文别名），供补全使用。
            "shell_aliases": sorted(VERB_ALIASES.keys()),
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
            if verb == "help":
                return self._cmd_help(account, rest, state, raw, request_id)
            if not verb and not rest:
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
            if verb in ("wear", "wield", "remove", "unwield", "drop"):
                return self._cmd_item_action(account, verb, rest, state, raw, request_id)
            if verb in ("rest", "sleep"):
                return self._cmd_rest(account, verb, rest, state, raw, request_id)
            if verb == "bai":
                return self._cmd_bai(account, rest, state, raw, request_id)
            if verb == "learn":
                return self._cmd_learn(account, rest, state, raw, request_id)
            if verb == "practice":
                return self._cmd_practice(account, rest, state, raw, request_id)
            if verb == "climb":
                return self._cmd_climb(account, rest, state, raw, request_id)
            if verb == "jump":
                return self._cmd_jump(account, rest, state, raw, request_id)
            if verb == "revive":
                return self._cmd_revive(account, state, raw, request_id)
            if verb == "give":
                return self._cmd_give(account, rest, state, raw, request_id)
            if verb == "quests":
                return self._cmd_quests(account, rest, state, raw, request_id)
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
                    account, "unknown", False, self._unknown_lines(raw), state, raw, request_id
                )
            return self._reply(
                account,
                verb or "unknown",
                False,
                self._unknown_lines(raw),
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
        accepted = self._try_accept_quest(account, str(target["id"]), message)
        if accepted:
            lines.append(("info", f"接受支线：{accepted}。"))
        after = self._state(account) or state
        return self._reply(account, "ask", True, lines, after, raw, request_id)

    def _try_accept_quest(self, account: str, npc: str, message: str) -> str:
        """问话后尝试接取支线；是否算接取由后端动作判定，shell 只做路由。

        只有该 NPC 在内容里确实是某个任务的发放人时才调用，避免每次闲聊
        都产生一条被拒绝的意图。
        """
        quests = dict(getattr(self.session.content, "xkx_quests", {}) or {})
        giver = any(
            str((q.get("giver") or {}).get("id") or "") == npc for q in quests.values()
        )
        if not giver:
            return ""
        resp = self.session.perform(account, "xkx_quest_accept", {"npc": npc, "topic": message})
        if not resp.get("ok"):
            return ""
        pid = str((self._state(account) or {}).get("player", {}).get("entity_id") or "")
        records = getattr(self.session.state, "xkx_quests", {}) or {}
        for key, rec in sorted(records.items()):
            if str(rec.get("actor") or "") != pid:
                continue
            quest = quests.get(str(rec.get("quest") or "")) or {}
            if str((quest.get("giver") or {}).get("id") or "") != npc:
                continue
            if rec.get("status") == "accepted":
                return str(quest.get("name") or rec.get("quest") or "")
        return ""

    def _cmd_quests(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        """只读展示：任务定义来自内容包，进度来自后端世界状态。"""
        quests = dict(getattr(self.session.content, "xkx_quests", {}) or {})
        pid = str((state.get("player") or {}).get("entity_id") or "")
        records = getattr(self.session.state, "xkx_quests", {}) or {}
        lines: list[tuple[str, str]] = [("room", "【支线】")]
        if not quests:
            lines.append(("info", "当前内容包没有可接的支线。"))
            return self._reply(account, "quests", True, lines, state, raw, request_id)
        for qid in sorted(quests):
            quest = quests[qid]
            rec = records.get(f"{pid}|{qid}") or {}
            status = str(rec.get("status") or "available")
            label = {"available": "未接取", "accepted": "进行中", "completed": "已完成"}.get(
                status, status
            )
            target = str((quest.get("target") or {}).get("name") or "")
            item = str((quest.get("required_item") or {}).get("name") or "")
            text = f"{quest.get('name') or qid}：{label}"
            if target:
                text += f"；目标 {target}"
            if item:
                text += f"；需交付 {item}"
            if status == "accepted":
                text += f"（当前步骤：{rec.get('step') or 'asked'}）"
            lines.append(("info", text + "。"))
        return self._reply(account, "quests", True, lines, state, raw, request_id)

    def _cmd_give(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        raw_text = str(rest or "").strip()
        if not raw_text:
            return self._reply(
                account, "give", False,
                [("error", "用法：给 <人> <物品>，或 give <物品> to <人>。")],
                state, raw, request_id,
            )
        npcs = list(state.get("present_npcs") or [])
        item_text = ""
        target = None
        marker = re.split(r"\s+(?:to|给|交给)\s+", raw_text, maxsplit=1, flags=re.I)
        if len(marker) == 2:
            item_text = marker[0].strip()
            target, _left = self._match_npc_prefix(npcs, marker[1].strip())
        if target is None:
            target, remainder = self._match_npc_prefix(npcs, raw_text)
            if target is not None and remainder:
                item_text = remainder
        if target is None or not item_text:
            return self._reply(
                account, "give", False,
                [("error", "没找到要交给的人或物品；在场的有：" + "、".join(n["name"] for n in npcs))],
                state, raw, request_id,
            )
        wallet = list(((state.get("player") or {}).get("wallet") or {}).get("items") or [])
        hits = [row for row in wallet if item_text in (row.get("item"), row.get("name"))
                or str(row.get("name") or "").startswith(item_text)]
        if not hits:
            return self._reply(
                account, "give", False,
                [("error", f"你身上没有「{item_text}」。")], state, raw, request_id,
            )
        item = str(hits[0].get("item") or "")
        resp = self.session.perform(
            account, "xkx_quest_deliver", {"npc": str(target["id"]), "item": item}
        )
        if not resp.get("ok"):
            return self._reply(
                account, "give", False,
                [("error", self._reason(str(resp.get("reject_reason") or ""), fallback="东西没有交出去。"))],
                state, raw, request_id,
            )
        after = self._state(account) or state
        pid = str((after.get("player") or {}).get("entity_id") or "")
        records = getattr(self.session.state, "xkx_quests", {}) or {}
        done = [
            rec for rec in records.values()
            if str(rec.get("actor") or "") == pid and rec.get("status") == "completed"
        ]
        lines: list[tuple[str, str]] = [
            ("info", f"你把{self._item_name(item)}交给{target['name']}。")
        ]
        if done:
            rec = sorted(done, key=lambda r: int(r.get("completed_tick") or 0))[-1]
            quest = dict(getattr(self.session.content, "xkx_quests", {}) or {}).get(
                str(rec.get("quest") or "")
            ) or {}
            reward = rec.get("reward") or {}
            parts = []
            if int(reward.get("exp") or 0) > 0:
                parts.append(f"经验 {int(reward['exp'])}")
            if int(reward.get("potential") or 0) > 0:
                parts.append(f"潜力 {int(reward['potential'])}")
            if int(reward.get("score") or 0) > 0:
                parts.append(f"声望 {int(reward['score'])}")
            for reward_item in (quest.get("reward") or {}).get("items") or []:
                parts.append(self._item_name(str(reward_item)))
            text = f"完成支线：{quest.get('name') or rec.get('quest') or ''}。"
            if parts:
                text += "获得：" + "、".join(parts) + "。"
            lines.append(("info", text))
        return self._reply(account, "give", True, lines, after, raw, request_id)

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
        row, remainder = self._match_item_rows(goods, rest)
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
        row, remainder = self._match_item_rows(rows, rest)
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

    # 原版 look 的 combined()：按 id + "/" + short() 分组，数量 != 1 时输出
    # chinese_number(count) + query("unit") + short；单位由种族守护进程决定。
    _RACE_UNIT = {"飞禽": "只", "走兽": "只", "走畜": "只", "爬蛇": "只", "昆虫": "只", "游鱼": "条"}
    _CN_DIGITS = "零一二三四五六七八九"

    def _content_race(self, template: str) -> str:
        """模板的种族；内容包没写种族时按人类（原版 human 守护进程，单位「位」）。"""
        cache = getattr(self, "_npc_race_cache", None)
        if cache is None:
            cache = {
                str(npc.get("id")): str(npc.get("race") or "")
                for npc in getattr(self.session.content, "npcs", []) or []
            }
            self._npc_race_cache = cache
        return cache.get(str(template), "")

    @classmethod
    def _cn_count(cls, n: int) -> str:
        if n < 10:
            return cls._CN_DIGITS[n]
        if n < 20:
            return "十" + (cls._CN_DIGITS[n % 10] if n % 10 else "")
        tens, ones = divmod(n, 10)
        return cls._CN_DIGITS[tens] + "十" + (cls._CN_DIGITS[ones] if ones else "")

    def _npc_present_text(self, npcs: list[dict[str, Any]]) -> list[str]:
        """在场人物按 (模板, 名称, 状态) 分组；同模板多实例只写一次并标数量。"""
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in npcs:
            name = str(row.get("name") or "")
            status = str(row.get("status") or "alive")
            key = (str(row.get("template") or row.get("id") or ""), name, status)
            groups.setdefault(key, []).append(row)
        parts: list[str] = []
        for (template, name, status), rows in groups.items():
            # 原版 short() 在非 living 时拼 query("disable_type")；当前只有
            # 参考快照的 `unconcious` 一种，按 P4a 最小范围只渲染这一种。
            tag = " <昏迷不醒>" if status == "unconcious" else ""
            if len(rows) == 1:
                parts.append(name + tag)
                continue
            unit = self._RACE_UNIT.get(self._content_race(template), "位")
            parts.append(f"{self._cn_count(len(rows))}{unit}{name}{tag}")
        return parts

    def _match_fight_target(
        self, npcs: Iterable[dict[str, Any]], rest: str
    ) -> tuple[dict[str, Any] | None, int]:
        """按原版 present(arg) 的语义选目标：只取一只，库存 LIFO。

        - `fight 绿营清兵`：取库存第一只（复刻里即最后创建的实例）。
        - `fight 绿营清兵 2`：跳过前一只，取倒数第二只；越界即无目标。
        """
        raw = str(rest or "").strip()
        index = 0
        matched = re.fullmatch(r"(.+?)\s+(\d{1,3})", raw)
        if matched:
            raw, index = matched.group(1).strip(), int(matched.group(2))
        if not raw:
            return None, index
        target, _ = self._match_npc_prefix(npcs, raw)
        if target is None:
            # 原版按 id() 匹配，`qing bing` 这类英文 id 也要能选中。
            target, _ = self._match_npc_alias(npcs, raw)
        if target is None:
            return None, index
        key = (str(target.get("template") or target.get("id") or ""), str(target.get("name") or ""))
        candidates = [
            row
            for row in npcs
            if (str(row.get("template") or row.get("id") or ""), str(row.get("name") or "")) == key
        ]
        ordered = sorted(
            candidates,
            key=lambda row: (int(row.get("instance") or 0), str(row.get("id") or "")),
            reverse=True,
        )
        if index == 0:
            return ordered[0], 1
        if 1 <= index <= len(ordered):
            return ordered[index - 1], index
        return None, index

    def _content_aliases(self, template: str) -> tuple[str, ...]:
        cache = getattr(self, "_npc_alias_cache", None)
        if cache is None:
            cache = {
                str(npc.get("id")): tuple(str(a).lower() for a in (npc.get("aliases") or []))
                for npc in getattr(self.session.content, "npcs", []) or []
            }
            self._npc_alias_cache = cache
        return cache.get(str(template), ())

    def _match_npc_alias(
        self, npcs: Iterable[dict[str, Any]], raw: str
    ) -> tuple[dict[str, Any] | None, str]:
        """英文 id/别名整词匹配（`qing bing`、`bing`），失败返回 None。"""
        text = raw.lower()
        best: tuple[int, dict[str, Any] | None] = (0, None)
        for row in npcs:
            nid = str(row.get("id") or "")
            tail = nid.rstrip("/").rsplit("/", 1)[-1].lower()
            for key in (tail, str(row.get("name") or "").lower()) + self._content_aliases(
                str(row.get("template") or row.get("id") or "")
            ):
                if key and key == text and len(key) > best[0]:
                    best = (len(key), row)
        if best[1] is None:
            return None, raw
        return best[1], ""

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
        target, picked_index = self._match_fight_target(npcs, rest)
        if target is None and not rest.strip() and len(npcs) == 1:
            # 只说“打”时，场上只有一个人就默认对他出手。
            target = npcs[0]
            picked_index = 1
        if target is None:
            hint = "、".join(self._npc_present_text(npcs)) if npcs else "（这里没有别人）"
            label = self._first_word(rest)
            if picked_index and npcs:
                return self._reply(
                    account, "fight", False,
                    [("error", f"这里没有第{picked_index}个「{label or '人'}」。在场：{hint}。")],
                    state, raw, request_id,
                )
            if not label and npcs:
                return self._reply(
                    account, "fight", False,
                    [("info", f"要打谁？在场：{hint}。可用 fight <人> 指定。")],
                    state, raw, request_id,
                )
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
        combat = (resp.get("details") or {}).get("combat")
        if isinstance(combat, dict) and combat:
            lines.extend(self._combat_report_lines(combat, target))
        else:
            lines.append(("warn", "引擎没有返回战报（combat_log 缺失）。"))
        drops = (resp.get("details") or {}).get("loot")
        if isinstance(drops, list) and drops:
            lines.append(
                ("info", "掉落：" + "、".join(self._take_text(row) for row in drops) + "。")
            )
        death = (resp.get("details") or {}).get("death")
        if isinstance(death, dict) and death:
            lines.extend(self._death_lines(death))
        lines.extend(self._score_core_lines(after))
        reply = self._reply(account, "fight", True, lines, after, raw, request_id)
        reply["engine_details"] = dict(resp.get("details") or {})
        # 同模板多实例同屏时，台词无法区分；把实际目标与序号给客户端，
        # 便于 UI 高亮和回归测试分辨打的是哪一只（不改台词文本）。
        reply["target"] = target["id"]
        reply["target_instance"] = int(target.get("instance") or 0)
        reply["target_index"] = int(picked_index or 0)
        return reply

    def _death_lines(self, death: dict[str, Any]) -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = [
            ("error", "你倒下了，一缕魂魄被押入鬼门关。"),
        ]
        exp_loss = int(death.get("exp_loss") or 0)
        if exp_loss:
            lines.append(
                (
                    "warn",
                    f"死亡后果：实战经验 {int(death.get('combat_exp_before') or 0)} → "
                    f"{int(death.get('combat_exp_after') or 0)}（损失 {exp_loss}）。",
                )
            )
        pot_loss = int(death.get("potential_loss") or 0)
        if pot_loss:
            lines.append(("warn", f"潜能损失 {pot_loss}，余 {int(death.get('potential_after') or 0)}。"))
        lost = list(death.get("skills_lost") or [])
        if lost:
            lines.append(("warn", "技能各降一级并清零熟练度：" + "、".join(map(str, lost)) + "。"))
        dropped = list(death.get("dropped") or [])
        if dropped:
            lines.append(("info", "随身物品落在死亡地点：" + "、".join(map(str, dropped)) + "。"))
        lines.append(
            (
                "info",
                f"死亡计数：{int(death.get('death_count') or 0)}。输入「还阳」复活。",
            )
        )
        return lines

    def _combat_report_lines(self, combat: dict[str, Any], target: dict[str, Any]) -> list[tuple[str, str]]:
        """按引擎返回的 rounds[] 原样转述：回合号、每击结果、伤害与气血变化、胜负与超额伤害。"""
        lines: list[tuple[str, str]] = []
        rounds = list(combat.get("rounds") or [])
        for row in rounds:
            try:
                round_no = int(row.get("round") or 0)
            except (TypeError, ValueError):
                round_no = 0
            lines.append(("system", f"第{round_no}回合"))
            for act in row.get("actions") or []:
                who = self._combat_name(act.get("actor"), target)
                whom = self._combat_name(act.get("target"), target)
                result = str(act.get("result") or "")
                move = str(act.get("move") or "")
                move_text = str(act.get("move_text") or "")
                # 武功招式显示 `name`（如「白虹贯日」）；没有名字时退回原版种族
                # `combat_action` 文案，占位符在这里替换，玩家可见文本不残留 `$x`。
                if move and "$" not in move:
                    prefix = f"使出一招「{move}」，"
                    desc = ""
                else:
                    prefix = ""
                    desc = self._render_action_text(move_text, who, whom) if move_text else ""
                if result == "dodge":
                    line = f"  {desc}，但{whom}闪开了。" if desc else f"  {who}{prefix}{whom}闪开了。"
                    lines.append(("combat", line))
                elif result == "parry":
                    line = f"  {desc}，被{whom}架住了。" if desc else f"  {who}{prefix}{whom}架住了。"
                    lines.append(("combat", line))
                else:
                    damage = int(act.get("damage") or 0)
                    hp_before = int(act.get("hp_before") or 0)
                    hp_after = int(act.get("hp_after") or 0)
                    wound = "，受了伤" if act.get("wounded") else ""
                    if desc:
                        line = f"  {desc}，造成{damage}点伤害（气血{hp_before}→{hp_after}{wound}）。"
                    else:
                        line = f"  {who}{prefix}击中{whom}，伤害{damage}，气血{hp_before}→{hp_after}{wound}。"
                    lines.append(("combat", line))
        winner = str(combat.get("winner") or "")
        rounds_used = int(combat.get("rounds_used") or len(rounds))
        if winner == "attacker":
            lines.append(("combat", f"共{rounds_used}回合，你赢了，{target['name']}倒下了。"))
        elif winner == "defender":
            lines.append(("error", f"共{rounds_used}回合，你倒下了。"))
        else:
            lines.append(("warn", f"共{rounds_used}回合，没分胜负，谁也没倒下。"))
        hp = combat.get("hp") or {}
        player_hp = next((int(v) for k, v in hp.items() if str(k).startswith("player:")), None)
        target_hp = next((int(v) for k, v in hp.items() if not str(k).startswith("player:")), None)
        if player_hp is not None or target_hp is not None:
            lines.append(
                (
                    "info",
                    "气血：你{} / {} {}。".format(
                        player_hp if player_hp is not None else "?",
                        target["name"],
                        target_hp if target_hp is not None else "?",
                    ),
                )
            )
        overkill = combat.get("overkill") or {}
        if overkill:
            lines.append(
                (
                    "info",
                    "超额伤害："
                    + "、".join(
                        f"{self._combat_name(eid, target)} {int(raw or 0)} 点"
                        for eid, raw in sorted(overkill.items())
                    )
                    + "。",
                )
            )
        return lines

    @staticmethod
    def _combat_name(entity_id: Any, target: dict[str, Any]) -> str:
        eid = str(entity_id or "")
        return "你" if eid.startswith("player:") else str(target.get("name") or "对手")

    # ------------------------------------------------------------- 成长线与生死
    def _cmd_bai(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        target = str(rest or "").strip()
        if not target:
            return self._reply(
                account, "bai", False,
                [("error", "要拜谁为师？用法：bai 师父名")], state, raw, request_id,
            )
        resp = self.session.perform(account, "bai", {"target": target})
        if not resp.get("ok"):
            code = str(resp.get("reject_reason") or "")
            return self._reply(
                account, "bai", False,
                [("error", self._reason(code, fallback="这一拜没有成。"))],
                state, raw, request_id,
            )
        growth = dict((resp.get("details") or {}).get("growth") or {})
        master = str(growth.get("master_name") or target)
        sect = str(growth.get("sect_name") or "")
        text = f"你向{master}叩拜，正式列入门墙。"
        if sect:
            text += f" 门派：{sect}。"
        after = self._state(account) or state
        return self._reply(
            account, "bai", True, [("cmd", f"bai {target}"), ("npc", text)], after, raw, request_id
        )

    def _cmd_learn(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        tokens = [t for t in str(rest or "").split() if t not in ("from", "向", "跟", "从")]
        if len(tokens) < 2:
            return self._reply(
                account, "learn", False,
                [("error", "用法：learn 师父 技能 [次数]（原版：learn 师父 技能 次数）")],
                state, raw, request_id,
            )
        teacher, skill = tokens[0], tokens[1]
        times = 1
        if len(tokens) >= 3:
            try:
                times = max(1, int(tokens[2]))
            except ValueError:
                return self._reply(
                    account, "learn", False,
                    [("error", "次数必须是正整数。")], state, raw, request_id,
                )
        resp = self.session.perform(
            account, "learn", {"teacher": teacher, "skill": skill, "times": times}
        )
        if not resp.get("ok"):
            code = str(resp.get("reject_reason") or "")
            return self._reply(
                account, "learn", False,
                [("error", self._reason(code, fallback="师父没有教你。"))],
                state, raw, request_id,
            )
        growth = dict((resp.get("details") or {}).get("growth") or {})
        name = str(growth.get("skill_name") or skill)
        level_before = int(growth.get("level_before") or 0)
        level_after = int(growth.get("level_after") or 0)
        xp_after = int(growth.get("xp_after") or 0)
        limit = (level_after + 1) * (level_after + 1)
        lines = [
            ("cmd", f"learn {teacher} {skill} {times}"),
            (
                "info",
                f"你向{growth.get('teacher_name') or teacher}请教「{name}」{int(growth.get('times') or 0)} 次："
                f"耗潜能 {int(growth.get('times') or 0)}、精力 {int(growth.get('jing_cost') or 0)}。",
            ),
            (
                "info",
                f"「{name}」等级 {level_before} → {level_after}，学习点数 {xp_after}/{limit}。",
            ),
        ]
        after = self._state(account) or state
        return self._reply(account, "learn", True, lines, after, raw, request_id)

    def _cmd_practice(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        tokens = str(rest or "").split()
        if not tokens:
            return self._reply(
                account, "practice", False,
                [("error", "用法：practice 技能 [次数]")], state, raw, request_id,
            )
        skill = tokens[0]
        times = 1
        if len(tokens) >= 2:
            try:
                times = max(1, int(tokens[1]))
            except ValueError:
                return self._reply(
                    account, "practice", False,
                    [("error", "次数必须是正整数。")], state, raw, request_id,
                )
        resp = self.session.perform(account, "practice", {"skill": skill, "times": times})
        if not resp.get("ok"):
            code = str(resp.get("reject_reason") or "")
            return self._reply(
                account, "practice", False,
                [("error", self._reason(code, fallback="这一练没有进展。"))],
                state, raw, request_id,
            )
        growth = dict((resp.get("details") or {}).get("growth") or {})
        name = str(growth.get("skill_name") or skill)
        level_before = int(growth.get("level_before") or 0)
        level_after = int(growth.get("level_after") or 0)
        xp_after = int(growth.get("xp_after") or 0)
        limit = (level_after + 1) * (level_after + 1)
        lines = [
            ("cmd", f"practice {skill} {times}"),
            (
                "info",
                f"你运功练习「{name}」{int(growth.get('times') or 0)} 次，耗体力 "
                f"{int(growth.get('stamina_cost') or 0)}。",
            ),
            (
                "info",
                f"「{name}」等级 {level_before} → {level_after}，学习点数 {xp_after}/{limit}。",
            ),
        ]
        if growth.get("partial"):
            lines.append(("warn", "体力用尽，练习提前停止。"))
        after = self._state(account) or state
        return self._reply(account, "practice", True, lines, after, raw, request_id)

    def _cmd_climb(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        resp = self.session.perform(account, "climb", {"arg": str(rest or "")})
        if not resp.get("ok"):
            code = str(resp.get("reject_reason") or "")
            return self._reply(
                account, "climb", False,
                [("error", self._reason(code, fallback="爬不上去。"))],
                state, raw, request_id,
            )
        growth = dict((resp.get("details") or {}).get("growth") or {})
        lines = [
            ("cmd", f"climb {rest}".strip()),
            (
                "info",
                f"你依仗轻功（{int(growth.get('dodge') or 0)} 级）攀上树去，落在"
                f"{self._loc_name(str(growth.get('to') or ''))}。",
            ),
        ]
        after = self._state(account) or state
        return self._reply(account, "climb", True, lines, after, raw, request_id)

    def _cmd_jump(
        self, account: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        resp = self.session.perform(account, "jump", {"arg": str(rest or "")})
        if not resp.get("ok"):
            code = str(resp.get("reject_reason") or "")
            return self._reply(
                account, "jump", False,
                [("error", self._reason(code, fallback="跳不过去。"))],
                state, raw, request_id,
            )
        growth = dict((resp.get("details") or {}).get("growth") or {})
        dodge = int(growth.get("dodge") or 0)
        landing = str(growth.get("landing") or "")
        if growth.get("fell"):
            text = f"你纵身一跃，轻功（{dodge} 级）不足百级，收势不住，跌进了{landing}。"
        else:
            text = f"你借木桩发力，轻功（{dodge} 级）稳稳掠过水面，落在{landing}。"
        lines = [
            ("cmd", f"jump {rest}".strip()),
            ("info", text),
        ]
        after = self._state(account) or state
        return self._reply(account, "jump", True, lines, after, raw, request_id)

    def _cmd_revive(
        self, account: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        resp = self.session.perform(account, "revive", {})
        if not resp.get("ok"):
            code = str(resp.get("reject_reason") or "")
            return self._reply(
                account, "revive", False,
                [("error", self._reason(code, fallback="你还阳不了。"))],
                state, raw, request_id,
            )
        growth = dict((resp.get("details") or {}).get("growth") or {})
        lines = [
            ("cmd", "还阳"),
            (
                "info",
                f"你从鬼门关还阳，回到{self._loc_name(str(growth.get('to') or ''))}。"
                f"（累计死亡 {int(growth.get('death_count') or 0)} 次）",
            ),
        ]
        after = self._state(account) or state
        return self._reply(account, "revive", True, lines, after, raw, request_id)

    @staticmethod
    def _render_action_text(text: Any, who: str, whom: str) -> str:
        """替换原版 `combat_action` 的 `$N/$n/$l` 等占位符，不把 `$x` 露给玩家。"""
        mapping = {
            "N": who, "n": whom, "p": "对方", "P": "对方",
            "l": "要害", "w": "", "W": "", "d": "外",
        }
        rendered = re.sub(
            r"\$[A-Za-z]", lambda m: mapping.get(m.group(0)[1:], ""), str(text or "")
        )
        return rendered.replace("$", "").strip()

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
        rows = self._ground_rows(state)
        want = rest.strip()
        if not rows:
            return self._reply(
                account, "loot", True,
                [("info", "地上没有可捡的东西。")],
                state, raw, request_id,
            )
        payload: dict[str, Any] = {}
        if want and want not in ("all", "全部", "所有", "都"):
            match, _tail = self._match_item_rows(
                [
                    dict(row, name=self._item_name(str(row.get("item") or "")))
                    for row in rows
                ],
                want,
            )
            if match is None:
                available = "、".join(self._ground_text(row) for row in rows) if rows else "（地上没有东西）"
                return self._reply(
                    account, "loot", False,
                    [("error", f"地上没有「{want}」。地上现有：{available}。")],
                    state, raw, request_id,
                )
            payload = {"item": match["item"]}
        resp = self.session.perform(account, "loot", payload)
        if not resp.get("ok"):
            return self._reply(
                account, "loot", False,
                [("error", self._reason(str(resp.get("reject_reason") or ""), fallback="没有捡到东西。"))],
                state, raw, request_id,
            )
        after = self._state(account) or state
        taken = (resp.get("details") or {}).get("loot")
        lines: list[tuple[str, str]] = []
        if isinstance(taken, list) and taken:
            lines.append(("info", "捡起：" + "、".join(self._take_text(row) for row in taken) + "。"))
        else:
            lines.append(("info", "地上没有可捡的东西。"))
        remaining = self._ground_rows(after)
        if remaining:
            lines.append(
                ("info", "地上还剩：" + "、".join(self._ground_text(row) for row in remaining) + "。")
            )
        else:
            lines.append(("info", "地上已经空了。"))
        wallet_items = list(((after.get("player") or {}).get("wallet") or {}).get("items") or [])
        if wallet_items:
            lines.append(
                (
                    "info",
                    "行囊："
                    + "、".join(f"{row['name']}×{row['qty']}" for row in wallet_items)
                    + "。",
                )
            )
        return self._reply(account, "loot", True, lines, after, raw, request_id)

    @staticmethod
    def _bag_hint(items: Iterable[dict[str, Any]]) -> str:
        rows = [f"{row.get('name')}×{int(row.get('qty') or 0)}" for row in items]
        return "、".join(rows) if rows else "（空）"

    def _equip_summary_lines(self, state: dict[str, Any] | None) -> list[tuple[str, str]]:
        player = (state or {}).get("player") or {}
        equipment = player.get("equipment") or {}
        slots = [row for row in (equipment.get("slots") or []) if not row.get("empty")]
        lines: list[tuple[str, str]] = []
        if slots:
            lines.append(
                ("info", "已装备：" + "、".join(f"{row['slot_name']}={row['name']}" for row in slots) + "。")
            )
        else:
            lines.append(("info", "你身上没有穿戴任何装备。"))
        applied = dict(equipment.get("applied") or {})
        if applied:
            lines.append(
                ("info", "装备加成：" + "、".join(f"{key}={value}" for key, value in sorted(applied.items())) + "。")
            )
        weapon_type = str(equipment.get("weapon_type") or "")
        if weapon_type:
            lines.append(("info", f"兵器类型：{weapon_type}。"))
        return lines

    def _cmd_item_action(
        self, account: str, verb: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        """穿/持/脱/放下/丢：物品名匹配后交给引擎动作，响应带最新装备快照。"""
        player = state.get("player") or {}
        items = [
            row
            for row in (((player.get("wallet") or {}).get("items")) or [])
            if int(row.get("qty") or 0) > 0
        ]
        slots = [
            row
            for row in (((player.get("equipment") or {}).get("slots")) or [])
            if not row.get("empty")
        ]
        want = str(rest or "").strip()
        payload: dict[str, Any] = {}
        if verb in ("wear", "wield", "drop"):
            if not want:
                label = "要丢什么" if verb == "drop" else "要穿/持什么"
                return self._reply(
                    account, verb, False,
                    [("error", f"{label}？行囊：{self._bag_hint(items)}。")],
                    state, raw, request_id,
                )
            match, tail = self._match_item_rows(items, want)
            if match is None:
                return self._reply(
                    account, verb, False,
                    [("error", f"行囊里没有「{want}」。行囊：{self._bag_hint(items)}。")],
                    state, raw, request_id,
                )
            payload["item"] = str(match.get("item") or "")
            if verb == "drop":
                payload["qty"] = self._qty_from(tail)
        elif verb == "remove":
            if not slots:
                return self._reply(
                    account, verb, False,
                    [("error", "你身上没有穿戴任何装备。")],
                    state, raw, request_id,
                )
            if want:
                match, _tail = self._match_item_rows(slots, want)
                if match is None:
                    match = next(
                        (
                            row
                            for row in slots
                            if want in (str(row.get("slot_name") or ""), str(row.get("slot") or ""))
                        ),
                        None,
                    )
                if match is None:
                    return self._reply(
                        account, verb, False,
                        [("error", f"你身上没有「{want}」。已装备：" + "、".join(str(row.get("name")) for row in slots) + "。")],
                        state, raw, request_id,
                    )
                payload["item"] = str(match.get("item") or "")
            else:
                armor = [row for row in slots if str(row.get("slot") or "") != "weapon"]
                if not armor:
                    return self._reply(
                        account, verb, False,
                        [("error", "兵器要用 unwield（放下）卸下；你身上没有护具可脱。")],
                        state, raw, request_id,
                    )
                payload["item"] = str(armor[0].get("item") or "")
        resp = self.session.perform(account, verb, payload)
        if not resp.get("ok"):
            return self._reply(
                account, verb, False,
                [("error", self._reason(str(resp.get("reject_reason") or ""), fallback="动作没有生效。"))],
                state, raw, request_id,
            )
        after = self._state(account) or state
        name = self._item_name(str(payload.get("item") or "")) if payload.get("item") else ""
        lines: list[tuple[str, str]] = []
        if verb == "wear":
            lines.append(("info", f"你穿上了{name}。"))
        elif verb == "wield":
            lines.append(("info", f"你持起了{name}。"))
        elif verb == "remove":
            lines.append(("info", f"你脱下了{name}。"))
        elif verb == "unwield":
            lines.append(("info", "你放下了兵器。"))
        elif verb == "drop":
            lines.append(("info", f"你丢下了{name}×{int(payload.get('qty') or 1)}。"))
        lines.extend(self._equip_summary_lines(after))
        return self._reply(account, verb, True, lines, after, raw, request_id)

    def _cmd_rest(
        self, account: str, verb: str, rest: str, state: dict[str, Any], raw: str, request_id: str
    ) -> dict[str, Any]:
        """休息/睡觉：默认 4 小时，输入里的数字按 1-8 小时截断。"""
        match = re.search(r"\d+", str(rest or ""))
        hours = max(1, min(8, int(match.group()) if match else 4))
        resp = self.session.perform(account, verb, {"hours": hours})
        if not resp.get("ok"):
            return self._reply(
                account, verb, False,
                [("error", self._reason(str(resp.get("reject_reason") or ""), fallback="现在没法休息。"))],
                state, raw, request_id,
            )
        after = self._state(account) or state
        player = after.get("player") or {}
        ent = self._entity(after)
        lines: list[tuple[str, str]] = []
        if verb == "sleep":
            lines.append(("info", f"你睡了 {hours} 小时，醒来时精神饱满。"))
        else:
            lines.append(("info", f"你休息了 {hours} 小时。"))
        if ent is not None and int(getattr(ent, "max_hp", 0) or 0) > 0:
            lines.append(("info", f"气血：{int(ent.hp)}/{int(ent.max_hp)}"))
        lines.append(
            (
                "info",
                f"体力：{round(float(player.get('stamina') or 0), 1)}/"
                f"{round(float(player.get('max_stamina') or 0), 1)}",
            )
        )
        lines.append(("info", f"耗时：{float(resp.get('duration_hours') or 0):g} 小时。"))
        return self._reply(account, verb, True, lines, after, raw, request_id)

    def _ground_rows(self, state: dict[str, Any] | None) -> list[dict[str, Any]]:
        """当前房间引擎地面（state.ground），按 item id 排序，保持引擎顺序。"""
        rows = (state or {}).get("ground")
        if not isinstance(rows, list):
            loc_id = str(((state or {}).get("location") or {}).get("id") or "")
            rows = [
                {"item": key, "qty": int(qty)}
                for key, qty in sorted((self.session.state.ground.get(loc_id) or {}).items())
            ]
        return [row for row in rows if int(row.get("qty") or 0) > 0]

    def _item_name(self, item_id: str) -> str:
        row = (self.session.content.items or {}).get(item_id) or {}
        name = str(row.get("name") or "").strip()
        return name or item_id.rstrip("/").rsplit("/", 1)[-1] or item_id

    def _ground_text(self, row: dict[str, Any]) -> str:
        name = self._item_name(str(row.get("item") or ""))
        qty = int(row.get("qty") or 0)
        return f"{name}×{qty}" if qty > 1 else name

    def _take_text(self, row: dict[str, Any]) -> str:
        name = self._item_name(str(row.get("item") or ""))
        qty = int(row.get("qty") or 0)
        return f"{name}×{qty}" if qty > 1 else name

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
            return "战斗动作已注册，但场上还没有带气血的角色；先等世界创世给角色写入属性。"
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
            lines.append(("npc", "在场人物：" + "、".join(self._npc_present_text(npcs)) + "。"))
        ground = self._ground_items(loc_id, state)
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

    def _ground_items(self, loc_id: str, state: dict[str, Any] | None = None) -> list[str]:
        """地上可见物：以引擎地面为准（创世已把内容包摆放物落成 loot_create）。

        只有快照里完全没有 `ground` 字段（旧世界/旧存档）时，才退回内容包静物描述，
        否则捡完的物品会被静态摆放重新“长”回地上。
        """
        if isinstance((state or {}).get("ground"), list):
            return [self._ground_text(row) for row in self._ground_rows(state)]
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
        martial = list(player.get("martial_skills") or [])
        if martial:
            lines.append(
                (
                    "info",
                    "武学："
                    + "、".join(f"{s['name']} {s['level']}级" for s in martial),
                )
            )
        elif skills:
            lines.append(
                (
                    "info",
                    "技能："
                    + "、".join(f"{s['name']} {s['level']}级" for s in skills),
                )
            )
        lines.append(
            (
                "info",
                f"实战经验：{int(player.get('combat_exp') or 0)} / 潜能：{int(player.get('potential') or 0)}"
                f" / 已学点数：{int(player.get('learned_points') or 0)}",
            )
        )
        master = player.get("master") or {}
        if master.get("id"):
            lines.append(("info", f"师承：{master.get('name') or master.get('id')}"))
        if int(player.get("death_count") or 0):
            lines.append(("info", f"死亡：{int(player.get('death_count') or 0)} 次"))
        if str(player.get("status") or "") != "alive":
            lines.append(("warn", "你现在处于死亡状态，输入「还阳」复活。"))
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

    def _cmd_help(
        self,
        account: str,
        rest: str,
        state: dict[str, Any],
        raw: str,
        request_id: str,
    ) -> dict[str, Any]:
        text = str(rest or "").strip()
        if not text:
            lines = self._help_lines() + self._help_pack_overview()
        else:
            lines = self._help_answer(text)
        return self._reply(account, "help", True, lines, state, raw, request_id)

    def _help_pack_overview(self) -> list[tuple[str, str]]:
        if self.help_error:
            return [("warn", f"帮助内容包：暂不可用（{self.help_error}）")]
        status_counts = (self.help_counts or {}).get("status") or {}
        commands = int((self.help_counts or {}).get("commands") or len(self.help_commands))
        topics = int((self.help_counts or {}).get("topics") or len(self.help_topics))
        implemented = int(status_counts.get("已实现") or 0)
        pending = int(status_counts.get("待实现") or 0)
        excluded = int(status_counts.get("不做") or 0)
        verbs = sorted(set(VERB_ALIASES.values()))
        return [
            (
                "info",
                f"帮助内容包：{commands} 条命令（已实现 {implemented} / 待实现 {pending} / 不做 {excluded}）"
                f" · {topics} 个主题。",
            ),
            ("info", "状态语义：已实现=入口可用；待实现=写明原因；不做=写明原因，绝不把待实现当可用。"),
            ("info", "本文字壳已接入口：" + "、".join(verbs) + "。其余命令只能看说明，不能执行。"),
            ("info", "help 命令 / help 命令 <系统|关键词> / help <命令名|别名>"),
            ("info", "help 主题 / help <主题|关键词>"),
        ]

    def _help_answer(self, text: str) -> list[tuple[str, str]]:
        norm = text.strip()
        lower = norm.lower()
        if norm in ("命令", "commands", "cmd"):
            return self._help_systems()
        if lower.startswith("命令 ") or lower.startswith("command "):
            keyword = norm.split(" ", 1)[1].strip()
            return self._help_command_search(keyword)
        if norm in ("主题", "topics"):
            return self._help_topic_index()
        first = norm.split(" ", 1)[0]
        row = self._find_help_command(first)
        if row is not None:
            return self._help_command_detail(row)
        topic = self._find_help_topic(norm)
        if topic is not None:
            return self._help_topic_detail(topic)
        return self._help_search(norm)

    def _help_systems(self) -> list[tuple[str, str]]:
        if self.help_error:
            return [("warn", f"帮助内容包：暂不可用（{self.help_error}）")]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in self.help_commands:
            groups.setdefault(str(row.get("system") or "未分类"), []).append(row)
        lines: list[tuple[str, str]] = [
            ("room", f"【命令 · {len(self.help_commands)} 条】"),
        ]
        for system, rows in sorted(groups.items()):
            counts = {"已实现": 0, "待实现": 0, "不做": 0}
            for row in rows:
                status = str(row.get("status") or "")
                if status in counts:
                    counts[status] += 1
            lines.append(
                (
                    "info",
                    f"{system}（{len(rows)}）：已实现 {counts['已实现']} / "
                    f"待实现 {counts['待实现']} / 不做 {counts['不做']}",
                )
            )
        lines.append(("info", "输入 help 命令 <系统|关键词> 看逐条状态；输入 help <命令名> 看原因。"))
        return lines

    def _help_command_search(self, keyword: str) -> list[tuple[str, str]]:
        key = keyword.strip()
        key_lower = key.lower()
        if not key:
            return self._help_systems()
        hits: list[dict[str, Any]] = []
        for row in self.help_commands:
            hay_parts = [
                str(row.get("system") or ""),
                str(row.get("name") or ""),
                str(row.get("summary") or ""),
                *[str(a) for a in (row.get("aliases") or [])],
            ]
            if any(key_lower in part.lower() for part in hay_parts):
                hits.append(row)
        lines: list[tuple[str, str]] = [
            ("room", f"【命令 · 匹配「{key}」{len(hits)} 条】"),
        ]
        for row in hits[:50]:
            alias = [str(a) for a in (row.get("aliases") or [])]
            entry = self._shell_entry(row)
            mark = " · 本壳已接" if entry else " · 本壳未接"
            name = str(row.get("name") or "")
            alias_text = f"（{'、'.join(alias)}）" if alias else ""
            lines.append(
                (
                    "warn" if str(row.get("status")) != "已实现" else "info",
                    f"{name}{alias_text} {self._status_text(row)}{mark}",
                )
            )
        if len(hits) > 50:
            lines.append(("info", f"另有 {len(hits) - 50} 条未列出，请输入更精确的关键词。"))
        if not hits:
            lines.append(("info", "没有匹配的命令；输入 help 命令 看全部系统。"))
        return lines

    def _help_command_detail(self, row: dict[str, Any]) -> list[tuple[str, str]]:
        name = str(row.get("name") or "")
        alias = [str(a) for a in (row.get("aliases") or [])]
        title = f"{name}（{'、'.join(alias)}）" if alias else name
        entry = self._shell_entry(row)
        lines: list[tuple[str, str]] = [
            ("room", f"【命令 · {title}】"),
            ("info", f"系统：{row.get('system') or '未分类'} · 状态：{self._status_text(row)}"),
            ("info", f"参数：{row.get('params') or '—'}"),
            ("info", f"说明：{row.get('summary') or '—'}"),
        ]
        evidence = str(row.get("evidence") or "").strip()
        if evidence:
            lines.append(("info", f"依据：{evidence}"))
        if entry:
            lines.append(("info", f"本文字壳：已接入口（输入 {entry} 可用）。"))
        else:
            lines.append(
                (
                    "warn",
                    "本文字壳：未接入口（输入该命令只会看到这条说明，不会执行）。",
                )
            )
        return lines

    def _help_topic_index(self) -> list[tuple[str, str]]:
        if self.help_error:
            return [("warn", f"帮助内容包：暂不可用（{self.help_error}）")]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in self.help_topics:
            groups.setdefault(str(row.get("category") or "未分类"), []).append(row)
        lines: list[tuple[str, str]] = [
            ("room", f"【主题 · {len(self.help_topics)} 个】"),
        ]
        for category, rows in sorted(groups.items()):
            lines.append(("info", f"{category}（{len(rows)}）："))
            for row in rows:
                lines.append(
                    (
                        "info",
                        f"  {row.get('id')} · {row.get('title')} · {row.get('status') or '未注明'}",
                    )
                )
        lines.append(("info", "输入 help <主题|关键词> 看正文；管理向主题不向玩家展开。"))
        return lines

    def _help_topic_detail(self, row: dict[str, Any]) -> list[tuple[str, str]]:
        status = str(row.get("status") or "未注明")
        lines: list[tuple[str, str]] = [
            ("room", f"【主题 · {row.get('title') or row.get('id')}】"),
            ("info", f"分类：{row.get('category') or '未分类'} · 状态：{status}"),
        ]
        if status == "管理":
            lines.append(("warn", "管理向内容，不向玩家开放。"))
            return lines
        if status == "未开放":
            lines.append(("warn", "本主题尚未开放；以下为内容包说明。"))
        body = str(row.get("body") or "").strip()
        for part in body.splitlines():
            if part.strip():
                lines.append(("info", part.strip()))
        if not body:
            lines.append(("info", "这个主题还没有正文。"))
        return lines

    def _help_search(self, text: str) -> list[tuple[str, str]]:
        key = text.lower()
        cmd_hits = [
            row
            for row in self.help_commands
            if any(
                key in str(part).lower()
                for part in [
                    row.get("name"),
                    row.get("summary"),
                    row.get("system"),
                    *[str(a) for a in (row.get("aliases") or [])],
                ]
            )
        ]
        topic_hits = [
            row
            for row in self.help_topics
            if key in str(row.get("title") or "").lower()
            or key in str(row.get("body") or "").lower()
        ]
        lines: list[tuple[str, str]] = []
        if cmd_hits or topic_hits:
            lines.append(("room", f"【帮助搜索 · «{text}»】"))
            for row in cmd_hits[:12]:
                lines.append(
                    (
                        "info",
                        f"命令 {row.get('name')}（{'、'.join(str(a) for a in (row.get('aliases') or []))}）"
                        f"· {self._status_text(row)}",
                    )
                )
            for row in topic_hits[:8]:
                lines.append(
                    ("info", f"主题 {row.get('id')} · {row.get('title')} · {row.get('status')}")
                )
            return lines
        return [
            ("warn", f"帮助里没有「{text}」。"),
            ("info", "输入 help 命令 或 help 主题 查看目录。"),
        ]

    def _unknown_lines(self, raw: str) -> list[tuple[str, str]]:
        text = str(raw or "").strip()
        first = text.split(" ", 1)[0] if text else ""
        row = self._find_help_command(first)
        if row is not None:
            entry = self._shell_entry(row)
            name = str(row.get("name") or first)
            status = str(row.get("status") or "")
            if status == "已实现" and not entry:
                lines: list[tuple[str, str]] = [
                    (
                        "warn",
                        f"「{first}（{name}）」在引擎里已实现，但本文字壳未接入口，当前不能使用。",
                    )
                ]
            elif entry:
                lines = [("warn", f"「{first}」这个动作没能执行；输入 help {name} 看用法。")]
            else:
                lines = [("warn", f"「{first}」还没接上：{self._status_text(row)}。")]
            evidence = str(row.get("evidence") or "").strip()
            if evidence:
                lines.append(("info", f"依据：{evidence}"))
            lines.append(("info", f"输入 help {name} 看完整说明。"))
            return lines
        keys = list(VERB_ALIASES.keys())
        keys += [str(r.get("name") or "") for r in self.help_commands]
        keys += [str(a) for r in self.help_commands for a in (r.get("aliases") or [])]
        lowered = {key.lower(): key for key in keys if key}
        matches = difflib.get_close_matches(
            first.lower(), list(lowered.keys()), n=3, cutoff=0.5
        )
        if matches:
            guesses = "、".join(lowered[m] for m in matches)
            return [
                ("error", f"你没听懂「{text}」。你是想打「{guesses}」吗？"),
                ("info", "输入 help 看全部命令；输入 help 命令 <系统> 按分类查。"),
            ]
        return [
            ("error", f"你没听懂「{text}」。"),
            ("info", "输入 help 看全部命令；输入 help 命令 <系统> 按分类查。"),
        ]

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
            ("info", f"loot [物品]       捡起地上物品；不带名字则按引擎顺序全捡（也可用 get）。{loot_text}。"),
            ("info", "wear <护具>       穿上护具（按物品 equip_slot 进对应槽位）。"),
            ("info", "wield <兵器>      持起兵器（兵器统一进 weapon 槽，决定战斗技能类型）。"),
            ("info", "remove [护具]     脱下护具；不带名字则脱第一件非兵器装备。"),
            ("info", "unwield（放下）   放下手中兵器。"),
            ("info", "drop <物品> [数量]  把行囊物品丢到当前房间地面，可再 loot 捡回。"),
            ("info", "rest [小时] / sleep [小时]  休息或睡觉；默认 4 小时，范围 1-8。"),
            ("info", "bai <师父>        向在场师父拜师（原版 bai），加入师承/门派。"),
            ("info", "learn <师父> <技能> [次数]  向自己的师父请教技能，消耗潜能与精力。"),
            ("info", "practice <技能> [次数]      自行练习已学技能，消耗体力。"),
            ("info", "climb tree        石梁温宅：轻功≥50 才能爬树。"),
            ("info", "jump 木桩         玫瑰小亭：轻功≥100 上小岛，否则落湖里。"),
            ("info", "revive（还阳）    死亡后从鬼门关复活。"),
            ("info", "give <物品> to <人> / 给 <人> <物品>   把任务物品交给对方。"),
            ("info", "quests（任务）    看当前支线与接取/完成进度。"),
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
        row = (self.session.state.locations or {}).get(str(loc_id)) or {}
        return str(row.get("name") or loc_id or "未知地点")

    def _reason(self, code: str, fallback: str = "") -> str:
        code = str(code or "")
        if code in _EXTRA_REASON_TEXT:
            return _EXTRA_REASON_TEXT[code]
        # `_reason_text` 是 session 模块级函数：带参数的原因码（overloaded:…）
        # 与 REASON_TEXT 表都在那里，这里按白名单复用，未知码保留动作特定兜底。
        from .session import REASON_TEXT, _reason_text as session_reason_text

        if code in REASON_TEXT or code.startswith(("overloaded:", "slot_occupied:", "bad_arg:")):
            return session_reason_text(code)
        return fallback or f"操作没有成功（{code or '未知原因'}）。"

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
    def _item_keys(row: dict[str, Any]) -> list[str]:
        """一件物品可用于命令匹配的名字集合。"""
        keys = [
            str(row.get("name") or ""),
            str(row.get("slot_name") or ""),
            str(row.get("slot") or ""),
        ]
        item_id = str(row.get("item") or "")
        if item_id:
            keys.append(item_id)
            keys.append(item_id.rsplit("/", 1)[-1])
        aliases = row.get("aliases")
        if isinstance(aliases, str):
            keys.extend(aliases.split())
        elif isinstance(aliases, (list, tuple)):
            keys.extend(str(alias) for alias in aliases)
        return [key for key in keys if key]

    @classmethod
    def _match_item_rows(
        cls, rows: Iterable[dict[str, Any]], text: str
    ) -> tuple[dict[str, Any] | None, str]:
        """物品名匹配，`wear/wield/remove/unwield/loot/drop/buy/sell` 共用。

        原版这些命令统一走 `present(arg, me)`（`cmds/std/wear.lpc:26`、
        `cmds/std/drop.lpc` 等），本 mudlib 的 `id()` 是精确
        `member_array`（`feature/name.lpc:15-35`），`present()` 命中多件时
        返回第一件、不报歧义（`doc/efuns/present:11`）。这里保留“精确优先”，
        再把匹配放宽到子串；多件命中同样按行序取第一件。
        """
        raw = str(text or "").strip()
        if not raw:
            return None, ""
        # 末尾数量（`buy 铁锤 3`、`drop 粗布2`）单独拆出，不参与名称匹配。
        qty = ""
        numbered = re.fullmatch(r"(.*?)\s*(\d+)", raw)
        if numbered and numbered.group(1).strip():
            raw, qty = numbered.group(1).strip(), numbered.group(2)
        probe = raw.lower()
        partial: dict[str, Any] | None = None
        for row in rows:
            keys = [key.lower() for key in cls._item_keys(row)]
            if probe in keys:
                return row, qty
            if partial is None and any(probe in key for key in keys):
                partial = row
        return (partial, qty) if partial is not None else (None, "")

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
