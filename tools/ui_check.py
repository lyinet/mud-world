#!/usr/bin/env python3
"""W4 前端验收：不写世界，用 mock API + Playwright 跑主流程。

用法：
  python3 tools/ui_check.py                 # 跑全部验收并输出证据到 reports/w4-ui/
  python3 tools/ui_check.py --serve         # 只启动 mock 服务，方便手动打开
  python3 tools/ui_check.py --only desktop  # desktop | mobile | keyboard | auth | reasons | regression

mock 服务只改内存，不会写 runs/ 或 events.sqlite，也不会启动世界 tick。
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui" / "player"
EVIDENCE_DIR = ROOT / "reports" / "w4-ui"
SESSION_PY = ROOT / "src" / "wsim" / "api" / "session.py"
AUTH_PY = ROOT / "src" / "wsim" / "api" / "auth.py"


def _json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


LOCATIONS = {
    "market": {"name": "市集", "exits": [("pharmacy", "药铺"), ("teahouse", "茶楼"),
                                        ("escort_agency", "镖局")]},
    "pharmacy": {"name": "药铺", "exits": [("market", "市集")]},
    "teahouse": {"name": "茶楼", "exits": [("market", "市集")]},
    "escort_agency": {"name": "镖局", "exits": [("market", "市集")]},
}

# 全城图 mock：与真 API GET /api/world/map 同构（9 地点 / 11 无向边 / 22 方向），
# 名称用真服务地名，保证前端固定布局的名称宽度校验通过。
CITY_MAP_NODES = [
    {"id": "market", "name": "集市", "exits": ["academy", "inn", "pharmacy", "smithy", "teahouse"]},
    {"id": "academy", "name": "书院", "exits": ["market", "temple"]},
    {"id": "temple", "name": "城隍庙", "exits": ["academy", "inn", "pharmacy"]},
    {"id": "pharmacy", "name": "药铺", "exits": ["market", "temple"]},
    {"id": "inn", "name": "客栈", "exits": ["ferry", "market", "temple"]},
    {"id": "ferry", "name": "河渡", "exits": ["escort_agency", "inn"]},
    {"id": "escort_agency", "name": "镖局", "exits": ["ferry", "smithy"]},
    {"id": "smithy", "name": "铁匠铺", "exits": ["escort_agency", "market"]},
    {"id": "teahouse", "name": "茶棚", "exits": ["market"]},
]
CITY_MAP_EDGES = [
    ("market", "academy"), ("academy", "temple"), ("market", "inn"),
    ("market", "pharmacy"), ("pharmacy", "temple"), ("inn", "temple"),
    ("market", "smithy"), ("smithy", "escort_agency"), ("escort_agency", "ferry"),
    ("ferry", "inn"), ("market", "teahouse"),
]

# 商业面板 mock：与真服务 shop/escort 视图字段同名。
SHOP_BUY = [
    {"item": "flatbread", "name": "炊饼", "category": "food", "price": 3, "tax": 0,
     "total": 3, "stock": 6, "business": "biz:market:shop", "business_name": "市集杂货铺"},
    {"item": "noodle", "name": "汤面", "category": "food", "price": 5, "tax": 0,
     "total": 5, "stock": 4, "business": "biz:market:shop", "business_name": "市集杂货铺"},
    {"item": "coarse_cloth", "name": "粗布（一匹）", "category": "material", "price": 25,
     "tax": 1, "total": 26, "stock": 3, "business": "biz:market:shop",
     "business_name": "市集杂货铺"},
]
SHOP_NAMES = {"flatbread": "炊饼", "noodle": "汤面", "coarse_cloth": "粗布（一匹）",
              "rice": "米饭", "cleaver": "菜刀", "needle": "绣花针"}
SHOP_ITEM_PRICE = {"flatbread": 3, "noodle": 5, "coarse_cloth": 25, "rice": 4,
                   "cleaver": 40, "needle": 12}
SHOP_SELL_PRICE = {"flatbread": 2, "noodle": 3, "coarse_cloth": 15, "rice": 2,
                   "cleaver": 24, "needle": 7}
SHOP_TOTALS = {"stall": 345, "storefront": 1472, "branch": 3450}
SHOP_STOCK = {
    "stall": {"flatbread": 6, "noodle": 4, "rice": 3},
    "storefront": {"flatbread": 10, "noodle": 8, "rice": 6, "coarse_cloth": 4,
                   "cleaver": 2, "needle": 2},
    "branch": {"flatbread": 14, "noodle": 10, "rice": 8, "coarse_cloth": 6,
               "cleaver": 3, "needle": 3},
}
POSITION_WAGES = {"铁匠": 32, "绣娘": 24, "脚夫": 18, "掌柜": 36, "游医": 28,
                  "镖师": 30, "酒保": 20, "药童": 15, "货郎": 26}
# 缺货日样例：与真服务日结字段同名（tx / stockout / stockout_items / stock_units）。
LEDGER_STOCKOUT = [
    {"day": 8, "revenue": 0, "restock": 0, "wage": 32, "tax": 0, "net_profit": -32,
     "tx": 0, "trades": 0, "stockout": True, "stock_units": 0,
     "stockout_items": ["flatbread", "noodle"]},
    {"day": 9, "revenue": 40, "restock": 6, "wage": 0, "tax": 2, "net_profit": 32,
     "tx": 5, "trades": 5, "stockout": False, "stock_units": 9,
     "stockout_items": []},
]
LEDGER_SAMPLE = [
    {"day": 1, "revenue": 60, "restock": 18, "wage": 32, "tax": 3, "net_profit": 7, "trades": 9},
    {"day": 2, "revenue": 0, "restock": 0, "wage": 32, "tax": 0, "net_profit": -32, "trades": 0},
    {"day": 3, "revenue": 30, "restock": 0, "wage": 70, "tax": 1, "net_profit": -41, "trades": 4},
    {"day": 4, "revenue": 20, "restock": 25, "wage": 0, "tax": 1, "net_profit": -6, "trades": 3},
    {"day": 5, "revenue": 100, "restock": 20, "wage": 32, "tax": 5, "net_profit": 43, "trades": 12},
    {"day": 6, "revenue": 45, "restock": 40, "wage": 0, "tax": 2, "net_profit": 3, "trades": 6},
    {"day": 7, "revenue": 80, "restock": 10, "wage": 32, "tax": 4, "net_profit": 34, "trades": 10},
]
NPC_SKILL = {"npc:liu_sanniang": 0.30, "npc:wang_tiejiang": 0.40,
             "npc:afu": 0.20, "npc:chen_huolang": 0.05}
AFFECTION = {"npc:wang_tiejiang": 7.0, "npc:afu": 7.0, "npc:chen_huolang": 7.0}
# 信任单独一张表：婚姻裁决要同时看好感与信任。
TRUST = {"npc:wang_tiejiang": 7.0, "npc:afu": 45.0, "npc:chen_huolang": 7.0}
# 已与别人成婚的 NPC：用来验证 target_in_couple，不参与成功路径。
NPC_MARRIED = {"npc:afu": "npc:pharmacy_keeper"}

QUEST = {
    "id": "quest:silk_sample:0",
    "name": "送一匹粗布到药铺",
    "reward_money": 12,
    "giver_name": "刘三娘",
    "giver_location": "market",
    "target_location": "pharmacy",
    "deadline_day": 3,
    "can_accept_here": True,
    "acceptable": True,
    "expired": False,
    "status": "open",
    "accepted_by_me": False,
}

NPCS = {
    "market": [{"id": "npc:liu_sanniang", "name": "刘三娘", "role": "商户",
                "doing": "整理今日的布匹订单", "location": "market", "presence": "在场"},
               {"id": "npc:wang_tiejiang", "name": "王铁匠", "role": "铁匠",
                "doing": "守着炉子打铁", "location": "market", "presence": "在场"},
               {"id": "npc:afu", "name": "阿福", "role": "药童",
                "doing": "在铺子门口晒药", "location": "market", "presence": "在场"},
               {"id": "npc:chen_huolang", "name": "陈货郎", "role": "货郎",
                "doing": "挑着担子叫卖", "location": "market", "presence": "在场"}],
    "pharmacy": [{"id": "npc:pharmacy_keeper", "name": "药铺掌柜", "role": "掌柜",
                  "doing": "核对药柜库存", "location": "pharmacy", "presence": "在场"}],
    "teahouse": [{"id": "npc:teahouse_owner", "name": "茶楼老板", "role": "掌柜",
                  "doing": "招呼客人", "location": "teahouse", "presence": "在场"}],
    "escort_agency": [{"id": "npc:escort_boss", "name": "镖局总镖头", "role": "镖师",
                       "doing": "清点镖车", "location": "escort_agency", "presence": "在场"}],
}

# 世界动态固定样本：市集/药铺（玩家当前地点与相邻地点）各一条 important，
# 茶楼一条普通事件，用于验证强调样式与「附近动静」提示。
# verb 用后端真实内部动词（move / work / trade …），summary 仍是中文短语，
# 与真服务返回形状一致；UI 必须把 verb 映射成中文、且正文不出现英文下划线词。
FEED_EVENTS = [
    {"tick": 37, "actor": "王铁匠", "verb": "work", "place": "药铺",
     "summary": "王铁匠在药铺修好一口药碾。", "important": True},
    {"tick": 34, "actor": "刘三娘", "verb": "trade", "place": "市集",
     "summary": "刘三娘在市集清点新到的粗布。", "important": True},
    {"tick": 31, "actor": "茶楼老板", "verb": "talk", "place": "茶楼",
     "summary": "茶楼老板给客人续了三壶茶。", "important": False},
    {"tick": 28, "actor": "更夫", "verb": "move", "place": "市集",
     "summary": "更夫敲着梆子走过市集。", "important": False},
    {"tick": 25, "actor": "药铺掌柜", "verb": "cultivate", "place": "药铺",
     "summary": "药铺掌柜把陈皮摊在竹匾里。", "important": False},
]


AUTH_ERROR_TEXT = {
    "auth_bad_username": "用户名需为 3-32 位小写字母、数字、下划线或连字符。",
    "auth_bad_password": "口令长度需为 6-128 位。",
    "auth_username_taken": "该用户名已被注册。",
    "auth_invalid_credentials": "用户名或口令不正确。",
    "auth_missing_token": "缺少 Bearer 令牌。",
    "auth_invalid_token": "令牌无效或已注销。",
    "auth_expired_token": "令牌已过期；请重新登录。",
    "auth_account_mismatch": "请求账号与令牌账号不一致。",
    "auth_disabled": "当前为 legacy 匿名模式，鉴权接口不可用。",
}


def auth_error(code, tick=0):
    return {"ok": False, "code": code, "reject_reason": code,
            "reason_text": AUTH_ERROR_TEXT.get(code, code), "tick": tick}


def _int_arg(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def reject(code, text=""):
    """与真服务失败响应同形：reject_reason 由前端映射成中文。"""
    return 409, {"ok": False, "reason": code, "reject_reason": code,
                 "reason_text": text or code}


class MockWorld:
    def __init__(self, auth_enabled=False, upstream_ok=True):
        self.lock = threading.RLock()
        self.auth_enabled = auth_enabled
        self.upstream_ok = upstream_ok
        self.accounts = {}
        self.users = {}
        self.sessions = {}          # token -> {user, exp, session_id}
        self.token_seq = 0
        self.last_write_auth = ""
        self.refresh_calls = 0
        self.rotate_count = 0
        self.verb_calls = {}
        self.slow_verb = ""
        self.slow_ms = 0
        self.force_401 = 0
        self.write_auth_headers = []
        self.feed_calls = 0
        self.feed_headers = []
        self.feed_extra = []

    # ---------------------------------------------------------------- accounts
    def _new_account(self, account_id, display_name, archetype, created):
        return {
            "account_id": account_id,
            "display_name": display_name or "无名客",
            "archetype": archetype or "游侠",
            "role": "wanderer",
            "location": "market",
            "money": 300,
            "tick": 12,
            "event_seq": 1,
            "stamina": 82,
            "created": created,
            "accepted": False,
            "items": {"flatbread": 3, "coarse_cloth": 1},
            "shop": None,
            "escort_orders": [],
            "stock_shift": 0,
            "stock_adj": {},
            "affection": dict(AFFECTION),
            "trust": dict(TRUST),
            "marriage": {"status": "single", "spouse_id": "", "spouse_name": "",
                         "since_tick": 0, "last_daily_tick": 0, "daily_note": ""},
            "replies": [],
            "sect": None,
            "sect_missing": False,
            "reputation": 0.2,
            "skill_max": 0.35,
        }

    def enter(self, account_id, display_name, archetype):
        with self.lock:
            created = account_id not in self.accounts
            if created:
                self.accounts[account_id] = self._new_account(account_id, display_name, archetype, True)
            return {"ok": True, "details": {"created": created,
                                            "endowment": {"granted": created}}}

    def account(self, account_id):
        return self.accounts.get(account_id)

    def state(self, account_id):
        with self.lock:
            a = self.account(account_id)
            if not a:
                return None
            loc = a["location"]
            active = bool(a["accepted"])
            row = dict(QUEST)
            row["can_accept_here"] = (not active and loc == QUEST["giver_location"])
            row["acceptable"] = not active
            if active:
                row.update({"status": "accepted", "accepted_by_me": True,
                            "can_accept_here": False, "acceptable": False})
            rows = [] if active else [row]
            active_rows = [dict(row, status="accepted", accepted_by_me=True,
                               can_accept_here=False, acceptable=False)] if active else []
            exits = [{"id": x[0], "name": x[1], "exists": True} for x in LOCATIONS[loc]["exits"]]
            next_action = ("把粗布送到药铺，交给药铺掌柜（12 文）" if active
                           else "接「送一匹粗布到药铺」，发布人刘三娘就在市集（12 文）")
            p = {
                "entity_id": "player:" + account_id,
                "account_id": account_id,
                "name": a["display_name"],
                "archetype": a["archetype"],
                "role": a["role"],
                "stamina": a["stamina"],
                "max_stamina": 100,
                "location": loc,
                "location_name": LOCATIONS[loc]["name"],
                "wallet": {"money": a["money"], "items": self._wallet_items(a)},
                "relations": self._relations(a),
                "skills": {"镖师": a.get("skill_max", 0.35)},
            }
            st = {
                "ok": True,
                "account_id": account_id,
                "tick": a["tick"],
                "readonly": False,
                "player": p,
                "location": {"id": loc, "name": LOCATIONS[loc]["name"], "exits": exits},
                "present_npcs": [
                    dict(npc,
                         bond=float(a["affection"].get(npc["id"], 0.0)),
                         trust=float(a["trust"].get(npc["id"], 0.0)),
                         skills={"name": npc["role"],
                                 "level": NPC_SKILL.get(npc["id"], 0.3)})
                    for npc in NPCS[loc]
                ],
                "nearby": NPCS[loc],
                "marriage": dict(a["marriage"]),
                "shop": self._shop_view(a),
                "escort_orders": [dict(o) for o in a["escort_orders"]],
                "quest_board": {
                    "rows": rows,
                    "active": active_rows,
                    "counts": {"open": 0 if active else 1,
                               "acceptable_here": 1 if (not active and loc == QUEST["giver_location"]) else 0,
                               "accepted_by_me": 1 if active else 0},
                },
                "today": {
                    "tick": a["tick"],
                    "next_action": next_action,
                    "quests": [{"name": QUEST["name"], "reward_money": QUEST["reward_money"],
                                "giver_name": QUEST["giver_name"]}] if not active else [],
                    "skills": [{"skill": "算账", "current": 1, "next_at": 2, "hours_to_next": 4}],
                    "npcs": [{"name": "刘三娘", "role": "商户"}],
                    "business": [{"shop_type": "stall", "cost": 120, "affordable": True, "blocked": False}],
                },
                "growth": {
                    "reputation": {"level": 1, "name": "初出茅庐",
                                   "value": a.get("reputation", 0.2),
                                   "progress": a.get("reputation", 0.2),
                                   "next_at": 1.0, "next_name": "小有名气", "next_unlock": "解锁茶楼委托"},
                    "skill": {"level": 1, "name": "手艺人", "value": 0.3, "progress": 0.3,
                              "next_at": 2.0, "next_name": "熟练工", "next_unlock": "工时缩短",
                              "skills": {"镖师": a.get("skill_max", 0.35)}},
                    "assets": {"level": 1, "name": "空手", "value": a["money"], "progress": 0.1,
                               "next_at": 1000, "next_name": "有摊", "next_unlock": "可买摊位"},
                },
                "online_players": [],
            }
            if not a.get("sect_missing"):
                st["sect"] = {"in_sect": bool(a.get("sect")), "sect": a.get("sect")}
                p["sect"] = st["sect"]
            return st

    # ---------------------------------------------------------------- 门派
    def _sect_state(self, account_id, a, leader, settled=False, arrears=0, treasury=600):
        player_id = "player:" + account_id
        # 让名册里的 NPC 弟子有真实的好感/信任（前端从 player.relations 取）；
        # 阿福故意留低值，用来验证「当前/需要/差多少」的收徒门槛文案。
        a["affection"]["npc:wang_tiejiang"] = 35.0
        a["trust"]["npc:wang_tiejiang"] = 31.0
        a["affection"]["npc:afu"] = 12.0
        a["trust"]["npc:afu"] = 8.0
        npc_rows = [
            {"id": "npc:wang_tiejiang", "name": "王铁匠", "skill": 0.55,
             "presence": "在场", "at_sect": True, "arrears": int(arrears)},
            {"id": "npc:afu", "name": "阿福", "skill": 0.3,
             "presence": "在场", "at_sect": True, "arrears": 0},
        ]
        if leader:
            leader_id = player_id
            leader_name = a["display_name"]
            disciples = list(npc_rows)
        else:
            leader_id = "npc:liu_sanniang"
            leader_name = "刘三娘"
            disciples = [{"id": player_id, "name": a["display_name"],
                          "skill": a.get("skill_max", 0.35), "presence": "在场",
                          "at_sect": True, "arrears": int(arrears)},
                         npc_rows[0]]
        return {
            "id": "sect:ui_check", "faction_id": "sect:ui_check",
            "name": "青竹派", "location": a["location"],
            "location_name": LOCATIONS[a["location"]]["name"],
            "role": "leader" if leader else "disciple",
            "leader": leader_id, "leader_name": leader_name,
            "member_count": 1 + len(disciples),
            "treasury": {"account": "sect:ui_check", "balance": int(treasury)},
            "disciples": disciples,
            "limits": {"disciple_cap": 3, "dues_daily": 5,
                       "stipend_daily": 10, "arrears_expel": 30},
            "settled_today": bool(settled),
        }

    def set_sect(self, account_id, mode, money=None, reputation=None,
                 settled=None, arrears=None, treasury=None):
        with self.lock:
            a = self.accounts.get(account_id)
            if a is None:
                return 409, {"ok": False, "reason": "player_not_entered"}
            a["sect_missing"] = (mode == "missing")
            a["sect"] = None
            if money is not None:
                a["money"] = int(money)
            if reputation is not None:
                a["reputation"] = float(reputation)
            if mode == "leader":
                a["sect"] = self._sect_state(account_id, a, True,
                                             settled=bool(settled or False),
                                             arrears=arrears or 0,
                                             treasury=treasury or 600)
            elif mode == "disciple":
                a["sect"] = self._sect_state(account_id, a, False,
                                             settled=bool(settled or False),
                                             arrears=arrears or 0,
                                             treasury=treasury or 500)
            return 200, {"ok": True, "mode": mode, "sect": a["sect"]}

    # ------------------------------------------------------------- commerce
    def _npc(self, npc_id):
        for rows in NPCS.values():
            for npc in rows:
                if npc["id"] == npc_id:
                    return npc
        return None

    def _wallet_items(self, a):
        rows = []
        for item, qty in sorted(a["items"].items()):
            if qty <= 0:
                continue
            price = SHOP_ITEM_PRICE.get(item, 1)
            rows.append({"item": item, "name": SHOP_NAMES.get(item, item), "qty": qty,
                         "price": price,
                         "sell_price": SHOP_SELL_PRICE.get(item, max(1, price // 2))})
        return rows

    def _relations(self, a):
        rows = []
        for npc_id, aff in sorted(a["affection"].items()):
            npc = self._npc(npc_id)
            if npc:
                rows.append({"target": npc_id, "name": npc["name"], "kind": "familiar",
                             "affection": aff, "trust": a["trust"].get(npc_id, aff)})
        npc = self._npc("npc:liu_sanniang")
        if npc:
            rows.append({"target": npc["id"], "name": npc["name"], "kind": "familiar",
                         "affection": 0.0, "trust": 0.0})
        return rows

    def _shop_view(self, a):
        loc = a["location"]
        biz = []
        if loc == "market":
            biz.append({"id": "biz:market:shop", "name": "市集杂货铺", "kind": "service_shop",
                        "cash": 900, "location": loc})
        if loc == "escort_agency":
            biz.append({"id": "biz:escort_agency:service_escort", "name": "平安镖局",
                        "kind": "service_escort", "cash": 5000, "location": loc})
        own = a["shop"] if a["shop"] and a["shop"]["location"] == loc else None
        if own:
            biz.append({"id": own["id"], "name": own["name"], "kind": own["kind"],
                        "cash": own["cash"], "location": own["location"],
                        "employees": [dict(e) for e in own["employees"]]})
        buy, seen = [], set()
        if loc == "market":
            for row in SHOP_BUY:
                r = dict(row)
                r["stock"] = max(0, int(row["stock"]) - a["stock_shift"] -
                                int(a.get("stock_adj", {}).get(r["item"], 0)))
                r["buy_limit"] = min(r["stock"], 5)
                buy.append(r)
                seen.add(r["item"])
        if own:
            for item, qty in sorted(own["stock"].items()):
                if item in seen:
                    continue
                seen.add(item)
                stock = max(0, int(qty) - a["stock_shift"])
                price = SHOP_ITEM_PRICE.get(item, 1)
                buy.append({"item": item, "name": SHOP_NAMES.get(item, item),
                            "category": "material" if item in ("cleaver", "needle", "coarse_cloth") else "food",
                            "price": price, "tax": 0, "total": price, "stock": stock,
                            "business": own["id"], "business_name": own["name"],
                            "buy_limit": stock})
        sell = []
        for item, qty in (sorted(a["items"].items()) if (loc == "market" or own) else []):
            if qty <= 0 or item not in SHOP_SELL_PRICE:
                continue
            price = SHOP_ITEM_PRICE.get(item, 1)
            sell.append({"item": item, "name": SHOP_NAMES.get(item, item), "category": "material",
                         "qty": qty, "price": price,
                         "sell_price": SHOP_SELL_PRICE[item], "sell_limit": qty})
        view = {"location": LOCATIONS[loc]["name"], "businesses": biz, "buy": buy, "sell": sell}
        if own:
            # W4 店铺经营：顶层现金/雇工/库存定价/7 日账本，与真服务字段同名。
            rows = []
            for item, qty in sorted(own["stock"].items()):
                base = int(own["base_prices"].get(item, SHOP_ITEM_PRICE.get(item, 1)))
                rows.append({
                    "item": item, "name": SHOP_NAMES.get(item, item),
                    "qty": max(0, int(qty) - a["stock_shift"]),
                    "price": int(own["prices"].get(item, base)),
                    "base_price": base,
                    "restock_price": base,
                    "sell_price": SHOP_SELL_PRICE.get(item, max(1, base // 2)),
                })
            view["cash"] = int(own["cash"])
            view["stock_count"] = sum(r["qty"] for r in rows)
            view["stock"] = rows
            view["employees"] = [dict(e) for e in own["employees"]]
            view["report"] = {"days": [dict(d) for d in own["report"]]}
        return view

    def _buy_row(self, a, item):
        for row in self._shop_view(a)["buy"]:
            if row["item"] == item:
                return row
        return None

    def feed(self, limit=20):
        try:
            cap = max(1, min(int(limit or 20), 50))
        except (TypeError, ValueError):
            cap = 20
        with self.lock:
            rows = [dict(r) for r in FEED_EVENTS] + [dict(r) for r in self.feed_extra]
        rows.sort(key=lambda r: -int(r.get("tick") or 0))
        return {"ok": True, "events": rows[:cap]}

    def push_feed(self, tick, summary, important=False, place="市集", verb="move"):
        with self.lock:
            self.feed_extra.append({
                "tick": int(tick), "actor": "更夫", "verb": verb,
                "place": place, "summary": summary, "important": bool(important),
            })

    def act(self, account_id, verb, args, client_tick):
        with self.lock:
            a = self.account(account_id)
            if not a:
                return 409, {"ok": False, "reason": "player_not_entered",
                             "reason_text": "你还没有进入世界。"}
            if client_tick is not None and Number_ok(client_tick) and int(client_tick) < a["tick"] - 120:
                return 409, {"ok": False, "reason": "stale_tick"}
            self.verb_calls[verb] = self.verb_calls.get(verb, 0) + 1
            if self.slow_verb == verb and self.slow_ms > 0:
                time.sleep(self.slow_ms / 1000.0)
            if verb == "move":
                target = (args or {}).get("to")
                allowed = [x[0] for x in LOCATIONS[a["location"]]["exits"]]
                if target not in allowed:
                    return 409, {"ok": False, "reason": "not_adjacent",
                                 "reason_text": "目标地点与当前位置不相邻。"}
                a["location"] = target
                a["tick"] += 6
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"],
                             "tick": a["tick"], "next_tick": a["tick"] + 6}
            if verb == "quest_accept":
                qid = (args or {}).get("quest")
                if qid != QUEST["id"]:
                    return 409, {"ok": False, "reason": "quest_not_found"}
                if a["accepted"]:
                    return 409, {"ok": False, "reason": "too_many_quests",
                                 "reason_text": "你手上已有进行中任务。"}
                if a["location"] != QUEST["giver_location"]:
                    return 409, {"ok": False, "reason": "not_at_giver"}
                a["accepted"] = True
                a["tick"] += 3
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"],
                             "tick": a["tick"], "next_tick": a["tick"] + 6}
            if verb == "quest_deliver":
                qid = (args or {}).get("quest")
                if qid != QUEST["id"]:
                    return 409, {"ok": False, "reason": "quest_not_found"}
                if not a["accepted"]:
                    return 409, {"ok": False, "reason": "quest_not_accepted_by_actor"}
                if a["location"] not in (QUEST["target_location"], QUEST["giver_location"]):
                    return 409, {"ok": False, "reason": "not_at_target",
                                 "reason_text": "还没到任务目标地点。"}
                a["accepted"] = False
                a["money"] += QUEST["reward_money"]
                a["tick"] += 4
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"],
                             "tick": a["tick"], "next_tick": a["tick"] + 6,
                             "details": {"reward_money": QUEST["reward_money"]}}
            if verb == "talk":
                target = str((args or {}).get("target") or "npc:liu_sanniang")
                npc = self._npc(target)
                if npc is None or npc["location"] != a["location"]:
                    return reject("not_together")
                # mock 不模拟每日上限，只验证 UI 的关系刷新与配偶标记。
                a["affection"][target] = min(100.0, float(a["affection"].get(target, 0)) + 2.0)
                a["trust"][target] = min(100.0, float(a["trust"].get(target, 0)) + 1.0)
                if a["marriage"].get("spouse_id") == target:
                    reply = npc["name"] + "（配偶）：锅里温着饭，早点回来。"
                else:
                    reply = npc["name"] + "：今日街上还算太平。"
                a["replies"].append({"from": npc["name"], "text": reply, "tick": a["tick"] + 2})
                a["tick"] += 2
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "details": {"reply": reply, "target": target}}
            if verb == "buy":
                item = str((args or {}).get("item") or "")
                qty = _int_arg((args or {}).get("qty"), 1)
                if qty is None:
                    return reject("bad_arg:qty")
                row = self._buy_row(a, item)
                if row is None:
                    return reject("item_not_available")
                if not 1 <= qty <= 20:
                    return reject("bad_arg:qty")
                if qty > row["stock"]:
                    return reject("item_missing")
                cost = int(row["total"]) * qty
                if a["money"] < cost:
                    return reject("not_enough_money")
                a["money"] -= cost
                a["items"][item] = a["items"].get(item, 0) + qty
                a["stock_adj"][item] = int(a["stock_adj"].get(item, 0)) + qty
                a["tick"] += 2
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"item": item, "qty": qty, "cost": cost}}
            if verb == "sell":
                item = str((args or {}).get("item") or "")
                qty = _int_arg((args or {}).get("qty"), 1)
                if qty is None:
                    return reject("bad_arg:qty")
                held = int(a["items"].get(item, 0))
                if not 1 <= qty <= 20:
                    return reject("bad_arg:qty")
                if qty > held:
                    return reject("item_missing")
                if not any(r["item"] == item for r in self._shop_view(a)["sell"]):
                    return reject("not_a_merchant")
                gain = int(SHOP_SELL_PRICE.get(item, 1)) * qty
                a["items"][item] = held - qty
                a["money"] += gain
                a["tick"] += 2
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"item": item, "qty": qty, "gain": gain}}
            if verb == "consume":
                foods = [r for r in self._shop_view(a)["buy"]
                         if r.get("category") in ("food", "drink") and r["stock"] > 0]
                foods.sort(key=lambda r: int(r["total"]))
                item = str((args or {}).get("item") or "")
                row = next((r for r in foods if r["item"] == item), None)
                if row is None:
                    row = foods[0] if foods else None
                if row is None:
                    return reject("item_not_available")
                cost = int(row["total"])
                if a["money"] < cost:
                    return reject("not_enough_money")
                before = a["stamina"]
                a["money"] -= cost
                a["stamina"] = min(100, a["stamina"] + 25)
                a["tick"] += 3
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"item": row["item"], "cost": cost,
                                         "stamina_before": before, "stamina_after": a["stamina"]}}
            if verb == "open_shop":
                kind = str((args or {}).get("shop_type") or "")
                if kind not in SHOP_TOTALS:
                    return reject("bad_shop_type")
                if a["shop"]:
                    return reject("already_owns_shop")
                total = SHOP_TOTALS[kind]
                if a["money"] < total:
                    return reject("insufficient_money")
                a["money"] -= total
                a["shop"] = {
                    "id": "shop:player:" + account_id,
                    "name": {"stall": "小摊", "storefront": "铺面", "branch": "分号"}[kind],
                    "kind": kind, "cash": 100, "location": a["location"],
                    "stock": dict(SHOP_STOCK[kind]), "employees": [],
                    "prices": {item: SHOP_ITEM_PRICE.get(item, 1) for item in SHOP_STOCK[kind]},
                    "base_prices": {item: SHOP_ITEM_PRICE.get(item, 1) for item in SHOP_STOCK[kind]},
                    "report": [],
                }
                a["tick"] += 3
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"shop_type": kind, "cost": total}}
            if verb == "recruit":
                target = str((args or {}).get("target") or "")
                npc = self._npc(target)
                if npc is None:
                    return reject("target_not_found")
                if target == "player:" + account_id:
                    return reject("self_recruit")
                own = a["shop"]
                if not own:
                    return reject("no_shop")
                if own["location"] != a["location"]:
                    return reject("wrong_location")
                if npc["location"] != own["location"]:
                    return reject("target_not_at_shop")
                cap = {"stall": 1, "storefront": 2, "branch": 3}.get(own["kind"], 0)
                if len(own["employees"]) >= cap:
                    return reject("shop_full")
                position = str((args or {}).get("position") or npc["role"])
                if position not in POSITION_WAGES:
                    return reject("unknown_position")
                if NPC_SKILL.get(target, 0) < 0.15:
                    return reject("skill_too_low")
                if AFFECTION.get(target, 0) < 6.0:
                    return reject("relation_too_low")
                market = POSITION_WAGES[position]
                wage = _int_arg((args or {}).get("wage"), market)
                if wage is None:
                    return reject("wage_too_low")
                if wage < int(round(market * 0.8)):
                    return reject("wage_too_low")
                if wage > int(round(market * 1.5)):
                    return reject("wage_too_high")
                own["employees"].append({"id": target, "name": npc["name"],
                                         "position": position, "wage": wage,
                                         "unpaid_wage": wage, "present": True})
                a["tick"] += 4
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"target": target, "position": position, "wage": wage}}
            if verb in ("restock", "set_price", "dismiss", "withdraw", "shop_report"):
                own = a["shop"]
                if not own or own["location"] != a["location"]:
                    return reject("not_shop_owner")
                if verb == "restock":
                    item = str((args or {}).get("item") or "")
                    if item not in own["stock"]:
                        return reject("item_not_available")
                    qty = _int_arg((args or {}).get("qty"), None)
                    if qty is None or qty < 1 or qty > 20:
                        return reject("bad_arg:qty")
                    base = int(own["base_prices"].get(item, 1))
                    total = base * qty
                    if a["money"] < total:
                        return reject("not_enough_money")
                    before = max(0, int(own["stock"].get(item, 0)) - a["stock_shift"])
                    a["money"] -= total
                    own["stock"][item] = before + qty
                    a["stock_shift"] = 0
                    a["tick"] += 3
                    a["event_seq"] += 1
                    return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                                 "next_tick": a["tick"] + 6,
                                 "details": {"item": item, "qty": qty, "total": total,
                                             "stock_before": before, "stock_after": before + qty}}
                if verb == "set_price":
                    item = str((args or {}).get("item") or "")
                    if item not in own["stock"]:
                        return reject("item_not_available")
                    price = _int_arg((args or {}).get("price"), None)
                    if price is None or price <= 0:
                        return reject("bad_arg:price")
                    base = int(own["base_prices"].get(item, 1))
                    low, high = int(base * 0.8), int(round(base * 1.2))
                    if price < low or price > high:
                        return reject("price_out_of_range")
                    own["prices"][item] = price
                    delta = int(round(((price - base) / base) * 100)) if base else 0
                    a["tick"] += 2
                    a["event_seq"] += 1
                    return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                                 "next_tick": a["tick"] + 6,
                                 "details": {"item": item, "price": price, "base_price": base,
                                             "delta_pct": delta}}
                if verb == "dismiss":
                    target = str((args or {}).get("target") or "")
                    row = next((e for e in own["employees"] if e["id"] == target), None)
                    if row is None:
                        return reject("target_not_employed")
                    owed = int(row.get("unpaid_wage", 0))
                    own["employees"].remove(row)
                    a["tick"] += 3
                    a["event_seq"] += 1
                    return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                                 "next_tick": a["tick"] + 6,
                                 "details": {"target": target, "wage_owed": owed}}
                if verb == "withdraw":
                    # 真服务 WithdrawAction 的参数名是 qty；amount 仅兼容旧请求。
                    amount = _int_arg((args or {}).get("qty"), None)
                    if amount is None:
                        amount = _int_arg((args or {}).get("amount"), None)
                    if amount is None or amount <= 0:
                        return reject("bad_arg:qty")
                    if amount > int(own["cash"]):
                        return reject("insufficient_shop_cash")
                    own["cash"] -= amount
                    a["money"] += amount
                    a["tick"] += 2
                    a["event_seq"] += 1
                    return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                                 "next_tick": a["tick"] + 6,
                                 "details": {"amount": amount, "shop_cash_before": own["cash"] + amount,
                                             "shop_cash_after": own["cash"]}}
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"], "next_tick": a["tick"] + 6,
                             "details": {
                                 "days": [dict(d) for d in own.get("report", [])],
                                 "stock": {item: int(qty) for item, qty in own["stock"].items()},
                                 "stockouts": sum(1 for d in own.get("report", []) if d.get("stockout")),
                             }}
            if verb == "escort":
                if not any(b["kind"].find("escort") >= 0
                           for b in self._shop_view(a)["businesses"]):
                    return reject("no_escort_agency")
                route = (args or {}).get("route") or []
                dest = route[1] if isinstance(route, list) and len(route) == 2 else None
                allowed = [x[0] for x in LOCATIONS[a["location"]]["exits"]]
                if not dest or route[0] != a["location"] or dest not in allowed:
                    return reject("route_not_adjacent")
                # 与真服务 session._args 一致：客户端只发 `cargo: {item, qty}`，
                # 顶层 cargo_item/cargo_qty 仅兼容旧请求。
                raw_cargo = (args or {}).get("cargo")
                if isinstance(raw_cargo, dict):
                    item = str(raw_cargo.get("item") or "")
                    qty = _int_arg(raw_cargo.get("qty"), 1)
                else:
                    item = str((args or {}).get("cargo_item") or "")
                    qty = _int_arg((args or {}).get("cargo_qty"), 1)
                if qty is None or not 1 <= qty <= 100:
                    return reject("bad_arg:cargo_qty")
                if qty > int(a["items"].get(item, 0)):
                    return reject("item_missing")
                fee = _int_arg((args or {}).get("fee"), 180)
                if fee is None or fee < 120:
                    return reject("fee_too_low")
                if fee > 260:
                    return reject("fee_too_high")
                value = _int_arg((args or {}).get("cargo_value"), 100)
                if value is None:
                    return reject("bad_arg:cargo_value")
                if value < 100:
                    return reject("cargo_too_low")
                a["tick"] += 1
                a["event_seq"] += 1
                a["escort_orders"].append({
                    "id": "escort:%d" % a["event_seq"], "carrier": "player:" + account_id,
                    "route": route, "status": "active", "fee": fee,
                    "agency_cut": 0.30, "arrive_tick": a["tick"] + 144,
                    "created_tick": a["tick"], "cargo_item": item, "cargo_qty": qty,
                })
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"route": route, "fee": fee,
                                         "arrive_tick": a["tick"] + 144}}
            # ------------------------------------------------------- 门派
            if verb == "found_sect":
                if a["sect"]:
                    return reject("already_in_sect")
                if a["money"] < 800:
                    return reject("insufficient_funds")
                if float(a.get("reputation", 0)) < 20:
                    return reject("insufficient_rep")
                a["money"] -= 800
                a["sect"] = self._sect_state(account_id, a, True,
                                             settled=False, arrears=0,
                                             treasury=600)
                a["sect"]["disciples"] = []
                a["sect"]["member_count"] = 1
                a["tick"] += 3
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"sect": {"in_sect": True, "sect": a["sect"]},
                                         "cost": 800}}
            if verb == "join_sect":
                if a["sect"]:
                    return reject("already_in_sect")
                a["sect"] = self._sect_state(account_id, a, False,
                                             settled=False, arrears=0,
                                             treasury=500)
                a["tick"] += 3
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"sect": {"in_sect": True, "sect": a["sect"]}}}
            if verb == "recruit_disciple":
                sect = a["sect"]
                if not sect:
                    return reject("no_sect")
                if sect.get("role") != "leader":
                    return reject("not_sect_leader")
                target = str((args or {}).get("target") or "")
                npc = self._npc(target)
                if npc is None:
                    return reject("disciple_not_found")
                if len(sect.get("disciples") or []) >= int((sect.get("limits") or {}).get("disciple_cap", 3)):
                    return reject("sect_full")
                if float(a["affection"].get(target, 0)) < 30:
                    return reject("favor_too_low")
                if float(a["trust"].get(target, 0)) < 30:
                    return reject("trust_too_low")
                sect.setdefault("disciples", []).append({
                    "id": target, "name": npc["name"], "skill": NPC_SKILL.get(target, 0.3),
                    "presence": "在场", "at_sect": True, "arrears": 0})
                sect["member_count"] = 1 + len(sect["disciples"])
                a["tick"] += 3
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"sect": {"in_sect": True, "sect": sect}}}
            if verb == "pay_disciples":
                sect = a["sect"]
                if not sect:
                    return reject("no_sect")
                if sect.get("role") != "leader":
                    return reject("not_sect_leader")
                if sect.get("settled_today"):
                    return reject("already_settled_today")
                limits = sect.get("limits") or {}
                count = len(sect.get("disciples") or [])
                dues = count * int(limits.get("dues_daily", 5))
                stipend = count * int(limits.get("stipend_daily", 10))
                sect.setdefault("treasury", {})["balance"] = int(
                    sect.get("treasury", {}).get("balance", 0)) + dues - stipend
                sect["settled_today"] = True
                a["tick"] += 3
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"settlement": {"dues": dues, "stipend": stipend,
                                                        "leavers": []},
                                         "sect": {"in_sect": True, "sect": sect}}}
            if verb == "expel_disciple":
                sect = a["sect"]
                if not sect:
                    return reject("no_sect")
                if sect.get("role") != "leader":
                    return reject("not_sect_leader")
                target = str((args or {}).get("target") or "")
                rows = sect.get("disciples") or []
                keep = [d for d in rows if str(d.get("id")) != target]
                if len(keep) == len(rows):
                    return reject("disciple_not_found")
                sect["disciples"] = keep
                sect["member_count"] = 1 + len(keep)
                a["tick"] += 3
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"sect": {"in_sect": True, "sect": sect}}}
            if verb == "sect_deposit":
                amount = _int_arg((args or {}).get("amount"), 0)
                sect = a["sect"]
                if not sect:
                    return reject("no_sect")
                if sect.get("role") != "leader":
                    return reject("not_sect_leader")
                if amount is None or amount < 1:
                    return reject("bad_arg:amount")
                if a["money"] < amount:
                    return reject("insufficient_funds")
                a["money"] -= amount
                sect["treasury"]["balance"] = int(sect["treasury"]["balance"]) + amount
                a["tick"] += 2
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"sect": {"in_sect": True, "sect": sect}}}
            if verb == "sect_withdraw":
                amount = _int_arg((args or {}).get("amount"), 0)
                sect = a["sect"]
                if not sect:
                    return reject("no_sect")
                if sect.get("role") != "leader":
                    return reject("not_sect_leader")
                if amount is None or amount < 1:
                    return reject("bad_arg:amount")
                if amount > 500:
                    return reject("withdraw_over_cap")
                balance = int(sect["treasury"]["balance"])
                if balance < amount:
                    return reject("insufficient_treasury")
                if balance - amount < 100:
                    return reject("treasury_reserve")
                sect["treasury"]["balance"] = balance - amount
                a["money"] += amount
                a["tick"] += 2
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"sect": {"in_sect": True, "sect": sect}}}
            if verb == "marriage_propose":
                target = str((args or {}).get("target") or "")
                npc = self._npc(target)
                m = a["marriage"]
                if npc is None:
                    return reject("target_not_found")
                if target == "player:" + account_id:
                    return reject("self_propose")
                if m.get("status") == "married":
                    return reject("already_married")
                if target in NPC_MARRIED and NPC_MARRIED[target] != target:
                    return reject("target_in_couple")
                if npc["location"] != a["location"]:
                    return reject("not_together")
                money = _int_arg((args or {}).get("gift_money"), 0)
                if money is None or money < 0:
                    return reject("bad_arg:gift_money")
                item = str((args or {}).get("gift_item") or "")
                qty = _int_arg((args or {}).get("gift_qty"), 1)
                if qty is None or qty < 1:
                    return reject("bad_arg:gift_qty")
                if item and int(a["items"].get(item, 0)) < qty:
                    return reject("gift_missing")
                if money > a["money"]:
                    return reject("not_enough_money")
                aff = float(a["affection"].get(target, 0))
                trust = float(a["trust"].get(target, 0))
                if aff >= 60.0 and trust >= 30.0:
                    decision = "accept"
                elif aff >= 45.0 or trust >= 20.0:
                    decision = "courtship"
                else:
                    decision = "reject"
                a["tick"] += 4
                a["event_seq"] += 1
                if decision == "accept":
                    a["money"] -= money
                    if item:
                        a["items"][item] = int(a["items"].get(item, 0)) - qty
                    m.update({"status": "married", "spouse_id": target,
                              "spouse_name": npc["name"], "since_tick": a["tick"],
                              "last_daily_tick": a["tick"], "daily_note": ""})
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"decision": decision,
                                         "marriage": dict(m)}}
            if verb == "marriage_end":
                m = a["marriage"]
                if m.get("status") != "married":
                    return reject("not_married")
                npc = self._npc(str(m.get("spouse_id") or ""))
                if npc is None or npc["location"] != a["location"]:
                    return reject("spouse_not_here")
                a["tick"] += 3
                a["event_seq"] += 1
                name = m.get("spouse_name")
                m.update({"status": "single", "spouse_id": "", "spouse_name": "",
                          "since_tick": 0, "last_daily_tick": 0, "daily_note": ""})
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"],
                             "next_tick": a["tick"] + 6,
                             "details": {"decision": "ended", "spouse_name": name,
                                         "marriage": dict(m)}}
            if verb in ("cultivate",):
                a["tick"] += 2
                a["event_seq"] += 1
                return 200, {"ok": True, "event_seq": a["event_seq"], "tick": a["tick"]}
            return 409, {"ok": False, "reason": "unknown_verb"}

    # -------------------------------------------------------------------- auth
    # 与 src/wsim/api/auth.py 的真实响应/错误码保持一致。
    def issue(self, username, session_id=None):
        self.token_seq += 1
        token = "tok_%s_%d" % (username, self.token_seq)
        now = time.time()
        sid = session_id or ("s:%d" % self.token_seq)
        self.sessions[token] = {"user": username, "exp": now + 3600, "session_id": sid}
        return {"ok": True, "account_id": username, "username": username,
                "session_id": sid, "token": token, "token_type": "Bearer",
                "expires_at": int(now + 3600), "expires_in": 3600}

    def token_error(self, header):
        if not header or not header.startswith("Bearer "):
            return "auth_missing_token"
        token = header[len("Bearer "):].strip()
        rec = self.sessions.get(token)
        if not rec:
            return "auth_invalid_token"
        if rec.get("exp", 0) <= time.time():
            return "auth_expired_token"
        return None

    def verify(self, header):
        if self.token_error(header):
            return None
        return self.sessions[header[len("Bearer "):].strip()]["user"]

    def rotate(self, header):
        """轮换令牌：旧 token 立即失效。"""
        code = self.token_error(header)
        if code:
            return None, code
        old = header[len("Bearer "):].strip()
        rec = self.sessions.pop(old)
        self.rotate_count += 1
        payload = self.issue(rec["user"], rec["session_id"])
        payload["rotated"] = True
        return payload, None

    def expire_all(self):
        with self.lock:
            self.sessions.clear()


def Number_ok(value):
    try:
        int(value)
        return True
    except Exception:
        return False


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, world=None, **kwargs):
        self.world = world
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _json(self, status, payload):
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _auth_error(self, status, code):
        self._json(status, auth_error(code, 12))

    def _require_write_auth(self):
        header = self.headers.get("Authorization") or ""
        self.world.write_auth_headers.append(header)
        if self.world.auth_enabled:
            code = self.world.token_error(header)
            if code:
                self._auth_error(401, code)
                return None
        self.world.last_write_auth = header
        return self.world.verify(header) or "anonymous"

    def _force_401_once(self):
        """测试用：让接下来的 n 个写请求返回 401（模拟并发轮换竞态）。"""
        with self.world.lock:
            if self.world.force_401 > 0:
                self.world.force_401 -= 1
                self._auth_error(401, "auth_expired_token")
                return True
        return False

    # -------------------------------------------------------------------- GET
    def do_GET(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._json(200, {"ok": True, "readonly": False, "run": "ui-check-mock",
                             "upstream": "mock", "upstream_ok": self.world.upstream_ok})
            return
        if path == "/api/auth/me":
            if not self.world.auth_enabled:
                self._auth_error(503, "auth_disabled")
                return
            code = self.world.token_error(self.headers.get("Authorization") or "")
            if code:
                self._auth_error(401, code)
                return
            header = self.headers.get("Authorization") or ""
            rec = self.world.sessions[header[len("Bearer "):].strip()]
            self._json(200, {"ok": True, "account_id": rec["user"], "username": rec["user"],
                             "session_id": rec["session_id"], "expires_at": int(rec["exp"]),
                             "status": "active", "disconnected": False})
            return
        if path == "/api/player/state":
            q = parse_qs(urlsplit(self.path).query)
            account = (q.get("account_id") or [""])[0]
            # 读端点匿名可用；令牌无效也不影响读取。
            st = self.world.state(account)
            if st is None:
                self._json(409, {"ok": False, "reason": "player_not_entered",
                                 "reason_text": "账号尚未进入世界。"})
                return
            self._json(200, st)
            return
        if path == "/api/world/feed":
            q = parse_qs(urlsplit(self.path).query)
            self.world.feed_calls += 1
            self.world.feed_headers.append(self.headers.get("Authorization") or "")
            self._json(200, self.world.feed((q.get("limit") or ["20"])[0]))
            return
        if path == "/api/world/map":
            nodes = [{"id": row["id"], "name": row["name"],
                      "exits": list(row["exits"]), "neighbors": list(row["exits"]),
                      "region": None, "region_name": None}
                     for row in CITY_MAP_NODES]
            edges = [{"from": a, "to": b, "walkable": True, "bidirectional": True}
                     for a, b in CITY_MAP_EDGES]
            self._json(200, {
                "ok": True, "tick": 40, "run": "ui-check-mock",
                "counts": {"locations": len(nodes), "edges": len(edges),
                           "directed_edges": len(edges) * 2},
                "nodes": nodes, "edges": edges,
                "reachable": [row["id"] for row in CITY_MAP_NODES],
                "region_supported": False,
                "region_note": "locations.yaml 没有区域字段，region 为 null。",
            })
            return
        if path == "/api/player/messages":
            q = parse_qs(urlsplit(self.path).query)
            account = (q.get("account_id") or [""])[0]
            since = int((q.get("since_tick") or ["0"])[0] or 0)
            with self.world.lock:
                a = self.world.accounts.get(account) or {}
                rows = [{"kind": "talk_reply", "from": r["from"], "text": r["text"],
                         "tick": r["tick"]}
                        for r in (a.get("replies") or []) if int(r.get("tick", 0)) >= since]
            self._json(200, {"ok": True, "messages": rows,
                             "next_since_tick": (rows[-1]["tick"] + 1) if rows else since})
            return
        if path == "/__check/feed_push":
            # keep_blank_values：允许 summary= 传空串，用来验证 verb→中文兜底。
            q = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            self.world.push_feed(
                (q.get("tick") or ["40"])[0],
                (q.get("summary") or ["更夫在街上敲了一声梆子。"])[0],
                (q.get("important") or ["0"])[0] in ("1", "true"),
                (q.get("place") or ["市集"])[0],
                (q.get("verb") or ["move"])[0],
            )
            self._json(200, {"ok": True, "rows": len(self.world.feed()["events"])})
            return
        if path == "/api/player/intents":
            self._json(404, {"ok": False, "reason": "intent_not_found"})
            return
        if path == "/__check/marriage":
            q = parse_qs(urlsplit(self.path).query)
            account = (q.get("account") or [""])[0]
            target = (q.get("target") or ["npc:wang_tiejiang"])[0]
            with self.world.lock:
                a = self.world.accounts.get(account)
                if a is None:
                    self._json(404, {"ok": False, "reason": "account_missing"})
                    return
                if "affection" in q:
                    a["affection"][target] = float(q["affection"][0])
                if "trust" in q:
                    a["trust"][target] = float(q["trust"][0])
                self._json(200, {"ok": True, "target": target,
                                 "affection": a["affection"].get(target, 0),
                                 "trust": a["trust"].get(target, 0)})
            return
        if path == "/__check/sect":
            q = parse_qs(urlsplit(self.path).query)
            account = (q.get("account") or [""])[0]
            mode = (q.get("mode") or ["none"])[0]

            def _num(key, cast):
                return cast(q[key][0]) if key in q else None

            status, payload = self.world.set_sect(
                account, mode,
                money=_num("money", int),
                reputation=_num("reputation", float),
                settled=(q.get("settled") or ["0"])[0] in ("1", "true", "yes"),
                arrears=_num("arrears", int),
                treasury=_num("treasury", int),
            )
            self._json(status, payload)
            return
        if path == "/__check/expire":
            self.world.expire_all()
            self._json(200, {"ok": True, "expired": True})
            return
        if path == "/__check/force401":
            q = parse_qs(urlsplit(self.path).query)
            self.world.force_401 = int((q.get("n") or ["1"])[0])
            self._json(200, {"ok": True, "force_401": self.world.force_401})
            return
        if path == "/__check/reset_stats":
            self.world.refresh_calls = 0
            self.world.rotate_count = 0
            self.world.force_401 = 0
            self.world.write_auth_headers = []
            self.world.verb_calls = {}
            self._json(200, {"ok": True})
            return
        if path == "/__check/verb_stats":
            q = parse_qs(urlsplit(self.path).query)
            verb = (q.get("verb") or [""])[0]
            self._json(200, {"ok": True, "verb": verb,
                             "calls": self.world.verb_calls.get(verb, 0)})
            return
        if path == "/__check/slow":
            q = parse_qs(urlsplit(self.path).query)
            self.world.slow_verb = (q.get("verb") or [""])[0]
            self.world.slow_ms = int((q.get("ms") or ["0"])[0])
            self._json(200, {"ok": True, "verb": self.world.slow_verb, "ms": self.world.slow_ms})
            return
        if path == "/__check/stats":
            self._json(200, {"ok": True, "refresh_calls": self.world.refresh_calls,
                             "rotate_count": self.world.rotate_count,
                             "force_401": self.world.force_401,
                             "write_auth_headers": self.world.write_auth_headers})
            return
        if path == "/__check/money":
            q = parse_qs(urlsplit(self.path).query)
            account = (q.get("account") or [""])[0]
            with self.world.lock:
                a = self.world.accounts.get(account)
                if a is None:
                    self._json(409, {"ok": False, "reason": "player_not_entered"})
                    return
                a["money"] = int((q.get("value") or ["0"])[0])
                self._json(200, {"ok": True, "account_id": account, "money": a["money"]})
            return
        if path == "/__check/tick":
            q = parse_qs(urlsplit(self.path).query)
            account = (q.get("account") or [""])[0]
            n = int((q.get("n") or ["6"])[0])
            shift = int((q.get("stock") or ["0"])[0])
            with self.world.lock:
                a = self.world.accounts.get(account)
                if a is None:
                    self._json(409, {"ok": False, "reason": "player_not_entered"})
                    return
                a["tick"] += n
                a["stock_shift"] += shift
                self._json(200, {"ok": True, "tick": a["tick"], "stock_shift": a["stock_shift"]})
            return
        if path == "/__check/shop_report":
            q = parse_qs(urlsplit(self.path).query)
            account = (q.get("account") or [""])[0]
            mode = (q.get("mode") or ["sample"])[0]
            with self.world.lock:
                a = self.world.accounts.get(account)
                if a is None or not a.get("shop"):
                    self._json(409, {"ok": False, "reason": "no_shop"})
                    return
                if "cash" in q:
                    a["shop"]["cash"] = int(q["cash"][0])
                if "stock" in q:
                    a["stock_adj"] = {}
                    hit = q["stock"][0]
                    stock = {}
                    for item in list(a["shop"]["stock"]):
                        if hit == "low" and item == "noodle":
                            stock[item] = 0
                        elif hit == "low" and item == "flatbread":
                            stock[item] = 1
                        elif hit == "low" and item == "rice":
                            stock[item] = 2
                        else:
                            stock[item] = int(SHOP_STOCK[a["shop"]["kind"]].get(item, 0))
                    a["shop"]["stock"] = stock
                if mode == "empty":
                    a["shop"]["report"] = []
                elif mode == "stockout":
                    a["shop"]["report"] = [dict(r) for r in LEDGER_STOCKOUT]
                else:
                    a["shop"]["report"] = [dict(r) for r in LEDGER_SAMPLE]
                self._json(200, {"ok": True, "days": len(a["shop"]["report"]),
                                 "stockouts": sum(1 for r in a["shop"]["report"]
                                                  if r.get("stockout")),
                                 "cash": a["shop"]["cash"]})
            return
        # 静态文件
        return super().do_GET()

    # ------------------------------------------------------------------- POST
    def do_POST(self):  # noqa: N802
        path = urlsplit(self.path).path
        body = self._read_json()
        if path.startswith("/api/auth/"):
            if not self.world.auth_enabled:
                # 真实服务 legacy 模式没有账号库：鉴权接口返回 503 auth_disabled。
                self._auth_error(503, "auth_disabled")
                return
            if path == "/api/auth/register":
                username = str(body.get("username") or "").strip().lower()
                password = str(body.get("password") or "")
                if not re.match(r"^[a-z0-9_-]{3,32}$", username):
                    self._auth_error(400, "auth_bad_username")
                    return
                if not (6 <= len(password) <= 128):
                    self._auth_error(400, "auth_bad_password")
                    return
                with self.world.lock:
                    if username in self.world.users:
                        self._auth_error(409, "auth_username_taken")
                        return
                    self.world.users[username] = password
                # 真实契约：register 只返回账号信息，不返回令牌。
                self._json(200, {"ok": True, "account_id": username,
                                 "username": username, "created": True})
                return
            if path == "/api/auth/login":
                username = str(body.get("username") or "").strip().lower()
                password = str(body.get("password") or "")
                if self.world.users.get(username) != password:
                    self._auth_error(401, "auth_invalid_credentials")
                    return
                self._json(200, self.world.issue(username))
                return
            if path == "/api/auth/refresh":
                self.world.refresh_calls += 1
                time.sleep(0.2)  # 放大并发窗口，用来验证前端单飞刷新
                payload, code = self.world.rotate(self.headers.get("Authorization") or "")
                if code:
                    self._auth_error(401, code)
                    return
                self._json(200, payload)
                return
            if path == "/api/auth/logout":
                header = self.headers.get("Authorization") or ""
                code = self.world.token_error(header)
                if code:
                    self._auth_error(401, code)
                    return
                rec = self.world.sessions.pop(header[len("Bearer "):].strip())
                self._json(200, {"ok": True, "logged_out": True,
                                 "session_id": rec["session_id"]})
                return
            self._json(404, auth_error("unknown_action"))
            return
        if path == "/api/player/enter":
            user = self._require_write_auth()
            if user is None:
                return
            if self._force_401_once():
                return
            account = str(body.get("account_id") or "").strip().lower()
            if not re.match(r"^[a-z0-9_-]{3,32}$", account):
                self._json(409, {"ok": False, "reason": "bad_account"})
                return
            self._json(200, self.world.enter(account, body.get("display_name"), body.get("archetype")))
            return
        if path == "/api/player/action":
            user = self._require_write_auth()
            if user is None:
                return
            if self._force_401_once():
                return
            status, payload = self.world.act(
                str(body.get("account_id") or ""),
                str(body.get("verb") or ""),
                body.get("args") or {},
                body.get("client_tick"),
            )
            self._json(status, payload)
            return
        self._json(404, {"ok": False, "reason": "unknown_action"})


def start_server(world):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    handler = partial(Handler, directory=str(UI_DIR), world=world)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, "http://127.0.0.1:%d" % port


class Results:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=None):
        row = {"name": name, "ok": bool(ok)}
        if detail is not None:
            row["detail"] = detail
        self.rows.append(row)
        print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)) if detail is not None else ""))
        return bool(ok)

    def must(self, name, ok, detail=None):
        good = self.add(name, ok, detail)
        if not good:
            raise AssertionError(name)
        return good

    def dump(self, path):
        path.write_text(json.dumps(self.rows, ensure_ascii=False, indent=2), encoding="utf-8")


def visible(page, selector):
    return page.evaluate("""(sel) => {
      const n = document.querySelector(sel);
      if (!n) return false;
      const r = n.getBoundingClientRect();
      const s = getComputedStyle(n);
      return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'
        && !n.classList.contains('hidden');
    }""", selector)


def wait_app(page):
    page.wait_for_function("() => !!window.__XKX && !!document.getElementById('entryOverlay')",
                           timeout=15000)
    page.wait_for_function("""() => {
      const tag = document.getElementById('runTag');
      return tag && tag.textContent !== '载入中' && window.__XKX.authState().mode !== 'unknown';
    }""", timeout=15000)


def enter_world(page, account="u_guide_desktop", display="验收客", auth_flow=None):
    wait_app(page)
    page.fill("#accountId", account)
    page.fill("#displayName", display)
    page.click("#enterBtn")
    if auth_flow:
        page.wait_for_function("() => !document.getElementById('authModal').classList.contains('hidden')",
                               timeout=8000)
        auth_flow(page)
    page.wait_for_function("() => document.getElementById('enterBtn').getAttribute('data-open') === '1'",
                           timeout=10000)
    page.click("#enterBtn")
    page.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                           timeout=8000)


def guide_snapshot(page):
    return page.evaluate("""() => {
      const g = window.__XKX.guideState();
      const t = window.__XKX.guideTargetInfo();
      const layer = document.getElementById('guideLayer');
      const card = document.getElementById('guideCard');
      const spot = document.getElementById('guideSpotlight');
      const rect = (n) => { if (!n) return null; const r = n.getBoundingClientRect();
        return {left:r.left, top:r.top, width:r.width, height:r.height, right:r.right, bottom:r.bottom}; };
      const cr = rect(card);
      const tr = t && t.rect;
      const overlap = !!(cr && tr) && !(cr.right + 6 <= tr.left || cr.left - 6 >= tr.right ||
                                        cr.bottom + 6 <= tr.top || cr.top - 6 >= tr.bottom);
      let sel = null;
      if (t) {
        if (t.id) sel = '#' + CSS.escape(t.id);
        else if (t.quest) sel = 'button[data-quest="' + CSS.escape(t.quest) + '"]';
        else if (t.exitId) sel = 'button[data-exit-id="' + CSS.escape(t.exitId) + '"]';
        else if (t.deliver) sel = 'button[data-deliver="' + CSS.escape(t.deliver) + '"]';
      }
      let hit = null;
      if (sel) {
        const node = document.querySelector(sel);
        if (node) {
          const r = node.getBoundingClientRect();
          const cx = r.left + r.width / 2;
          const cy = r.top + r.height / 2;
          const h = document.elementFromPoint(cx, cy);
          hit = {selector: sel, same: node === h || node.contains(h),
                 hitTag: h ? h.tagName : null, hitId: h ? (h.id || '') : null};
        }
      }
      const visibleButtons = Array.from(document.querySelectorAll('button,input,textarea,.exit,.npc'))
        .filter(n => { const r = n.getBoundingClientRect(); const s = getComputedStyle(n);
          return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; })
        .map(n => ({tag:n.tagName, id:n.id||'', text:(n.textContent||'').trim().slice(0,18),
                    h:Math.round(n.getBoundingClientRect().height)}));
      return {
        visible: !layer.classList.contains('hidden'),
        stepText: document.getElementById('guideStep').textContent,
        title: document.getElementById('guideTitle').textContent,
        why: document.getElementById('guideWhy').textContent,
        nextText: document.getElementById('guideNext').textContent,
        guide: g, target: t, card: cr, spotlight: rect(spot),
        cardOverlapsTarget: overlap, hit: hit, visibleButtons: visibleButtons,
        innerWidth: innerWidth, innerHeight: innerHeight,
        scrollWidth: document.documentElement.scrollWidth,
        scrollHeight: document.documentElement.scrollHeight,
        focusId: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null
      };
    }""")


def click_guide_target(page, info):
    t = info["target"]
    if not t:
        raise AssertionError("guide target missing")
    if t.get("id"):
        page.click("#" + t["id"])
    elif t.get("quest"):
        page.click('button[data-quest="%s"]' % t["quest"])
    elif t.get("exitId"):
        page.click('button[data-exit-id="%s"]' % t["exitId"])
    elif t.get("deliver"):
        page.click('button[data-deliver="%s"]' % t["deliver"])
    else:
        raise AssertionError("unknown guide target: %r" % t)


def capture(page, name, results, extra=None):
    path = EVIDENCE_DIR / (name + ".png")
    page.screenshot(path=str(path))
    if extra is not None:
        extra["screenshot"] = str(path.relative_to(ROOT))
    return extra


FEED_EVIDENCE = ROOT / "reports" / "w4-feed"


def feed_capture(page, name, results, extra=None):
    FEED_EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = FEED_EVIDENCE / (name + ".png")
    page.screenshot(path=str(path))
    if extra is not None:
        extra["screenshot"] = str(path.relative_to(ROOT))
    return extra


def feed_snapshot(page):
    return page.evaluate("""() => {
      const list = document.getElementById('feedList');
      const block = document.getElementById('worldFeed');
      const rows = Array.from(document.querySelectorAll('#feedList .feed-row'));
      const cs = getComputedStyle(list);
      const status = document.getElementById('feedStatus');
      return {
        blockVisible: !!(block && block.getBoundingClientRect().height > 0),
        count: rows.length, feedRows: (window.__XKX.feedState() || {}).rows,
        ticks: rows.map(r => Number(r.getAttribute('data-feed-tick'))),
        important: rows.filter(r => r.getAttribute('data-feed-important') === '1').length,
        texts: rows.map(r => r.textContent.trim()),
        height: Math.round(list.getBoundingClientRect().height),
        overflowY: cs.overflowY, scrollHeight: list.scrollHeight, clientHeight: list.clientHeight,
        scrollWidth: document.documentElement.scrollWidth,
        inFold: !!block.closest('.block.foldable'), hasFoldBtn: !!block.querySelector('.fold-btn'),
        announced: (window.__XKX.feedState() || {}).announced || 0,
        logWorld: document.querySelectorAll('#log .log-item.world').length,
        toast: (document.getElementById('toast') || {}).textContent || '',
        errors: document.querySelectorAll('#errorList .error-item').length,
        money: (document.getElementById('stMoney') || {}).textContent || '',
        placeholder: (list.querySelector('.feed-empty') || {}).textContent || '',
        statusHidden: status ? status.classList.contains('hidden') : true,
        statusText: status ? status.textContent : ''
      };
    }""")


def run_feed(browser, base, results, world):
    """世界动态：默认可见、倒序、固定高度、单独节流、失败降级、附近重要动静提示。"""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    try:
        page.goto(base + "/?ui_check=1")
        enter_world(page, "u_feed_desktop", "看戏客")
        # 跳过新手引导，避免提示层遮住世界动态区域
        page.evaluate("""() => { const b = document.getElementById('guideSkip'); if (b) b.click(); }""")
        page.wait_for_function(
            "() => document.getElementById('guideLayer').classList.contains('hidden')", timeout=8000)
        page.wait_for_function("() => window.__XKX.feedState().rows >= 5", timeout=15000)
        page.wait_for_function("() => window.__XKX.feedState().announced >= 2", timeout=8000)
        view = feed_snapshot(page)
        results.add("feed 默认可见（不在折叠组内）",
                    view["blockVisible"] and not view["inFold"] and not view["hasFoldBtn"], view)
        results.add("feed 20 条上限内、样本 5 条全渲染",
                    view["count"] == 5 and view["feedRows"] == 5, view)
        results.add("feed 按 tick 倒序", view["ticks"] == sorted(view["ticks"], reverse=True),
                    view["ticks"])
        results.add("feed 每行格式 · [tick] actor 在 place：summary",
                    all(re.match(r"^· \[\d+\] .+ 在 .+：", t) for t in view["texts"]),
                    view["texts"])
        results.add("feed important 使用强调样式", view["important"] == 2 and
                    "important" in page.evaluate(
                        "() => document.querySelector('#feedList .feed-row.important') ? 'important' : ''"),
                    {"important": view["important"]})
        results.add("feed 请求匿名、不带 Authorization",
                    bool(world.feed_calls) and all(h == "" for h in world.feed_headers),
                    {"feed_calls": world.feed_calls})
        results.add("feed 附近 important 走现有提示体系（toast + 操作记录）",
                    view["announced"] >= 2 and view["logWorld"] >= 1,
                    {"announced": view["announced"], "logWorld": view["logWorld"],
                     "toast": view["toast"]})
        feed_capture(page, "feed-desktop", results, view)

        # 固定高度容器 + 滚动锚定：用户滚到下方时插入新条目不应跳动
        # 先补几条让列表真正溢出（5 条时固定高度仍未满）
        page.evaluate("""() => Promise.all([40, 41, 42].map(t =>
            fetch('/__check/feed_push?tick=' + t + '&summary=' +
                  encodeURIComponent('街上有人走过。')).then(r => r.json())))""")
        page.evaluate("() => window.__XKX.refreshFeed()")
        page.wait_for_function("() => window.__XKX.feedState().rows >= 8", timeout=8000)
        results.add("feed 列表溢出时固定高度内滚动",
                    page.evaluate("() => { const l = document.getElementById('feedList');"
                                  "return l.scrollHeight > l.clientHeight; }"), {})
        page.evaluate("""() => { const l = document.getElementById('feedList'); l.scrollTop = l.scrollHeight; }""")
        before = page.evaluate("""() => { const l = document.getElementById('feedList');
            return {top: l.scrollTop, height: l.scrollHeight}; }""")
        page.evaluate("""() => fetch('/__check/feed_push?tick=43&important=0&summary=' +
            encodeURIComponent('更夫在西街敲了一声梆子。')).then(r => r.json())""")
        page.evaluate("() => window.__XKX.refreshFeed()")
        page.wait_for_function("() => window.__XKX.feedState().rows >= 9", timeout=8000)
        after = page.evaluate("""() => { const l = document.getElementById('feedList');
            return {top: l.scrollTop, height: l.scrollHeight}; }""")
        delta = after["height"] - before["height"]
        results.add("feed 下滚阅读时插入新条目不跳动",
                    before["top"] > 0 and abs(after["top"] - (before["top"] + delta)) <= 2,
                    {"before": before, "after": after, "delta": delta})

        # 在顶部时跟随最新
        page.evaluate("""() => { const l = document.getElementById('feedList'); l.scrollTop = 0; }""")
        page.evaluate("""() => fetch('/__check/feed_push?tick=45&important=1&summary=' +
            encodeURIComponent('一队镖车进了市集。')).then(r => r.json())""")
        page.evaluate("() => window.__XKX.refreshFeed()")
        page.wait_for_function("() => window.__XKX.feedState().rows >= 7", timeout=8000)
        top = page.evaluate("() => document.getElementById('feedList').scrollTop")
        results.add("feed 停在顶部时自动跟随最新一条", top == 0, {"scrollTop": top})
        results.add("feed important 新事件仍会提示",
                    page.evaluate("() => window.__XKX.feedState().announced") >= 3,
                    {"announced": page.evaluate("() => window.__XKX.feedState().announced")})

        # 内部动词 → 中文动作：空摘要走映射表，未知动词走「有事发生」，
        # 摘要里混入内部动词也要替换，且整份渲染文本不得出现 [a-z_]+。
        page.evaluate("""() => Promise.all([
            fetch('/__check/feed_push?tick=50&verb=move&summary=&place=' +
                  encodeURIComponent('药铺')).then(r => r.json()),
            fetch('/__check/feed_push?tick=51&verb=quest_accept&summary=&place=' +
                  encodeURIComponent('市集')).then(r => r.json()),
            fetch('/__check/feed_push?tick=52&verb=odd_job&summary=&place=' +
                  encodeURIComponent('茶楼')).then(r => r.json()),
            fetch('/__check/feed_push?tick=53&verb=move&summary=move&place=' +
                  encodeURIComponent('市集')).then(r => r.json())
        ])""")
        page.evaluate("() => window.__XKX.refreshFeed()")
        page.wait_for_function("() => window.__XKX.feedState().rows >= 14", timeout=8000)
        mapped = page.evaluate("""() => {
          const byTick = {};
          document.querySelectorAll('#feedList .feed-row').forEach(function (r) {
            byTick[r.getAttribute('data-feed-tick')] = r.textContent.trim();
          });
          return {lines: byTick,
                  all: Array.from(document.querySelectorAll('#feedList .feed-row'))
                         .map(function (r) { return r.textContent.trim(); })};
        }""")
        results.add("feed 空摘要按 verb 映射中文动作（move→赶路、quest_accept→接单）",
                    mapped["lines"].get("50", "").endswith("赶路") and
                    mapped["lines"].get("51", "").endswith("接单"), mapped["lines"])
        results.add("feed 未知动词兜底「有事发生」且不漏原始英文",
                    mapped["lines"].get("52", "").endswith("有事发生") and
                    "odd_job" not in mapped["lines"].get("52", ""), mapped["lines"])
        results.add("feed 摘要混入内部动词同样替换（move→赶路）",
                    mapped["lines"].get("53", "").endswith("赶路"), mapped["lines"])
        results.add("feed 渲染文本不含内部英文动词（[a-z_]+）",
                    all(not re.search(r"[a-z_]+", t) for t in mapped["all"]),
                    mapped["all"])

        # 失败降级：不弹红字、不动主状态，只显示「世界暂时安静」
        baseline = feed_snapshot(page)
        page.route("**/api/world/feed*", lambda route: route.abort())
        page.evaluate("() => window.__XKX.refreshFeed()")
        page.wait_for_function("() => window.__XKX.feedState().failed === true", timeout=8000)
        failed = feed_snapshot(page)
        results.add("feed 失败显示「世界暂时安静」占位",
                    (not failed["statusHidden"] and "世界暂时安静" in failed["statusText"]) or
                    "世界暂时安静" in failed["placeholder"], failed)
        results.add("feed 失败不弹红字、不写错误历史、主状态不变",
                    failed["errors"] == baseline["errors"] and failed["money"] == baseline["money"],
                    {"errors": [baseline["errors"], failed["errors"]],
                     "money": [baseline["money"], failed["money"]]})
        page.unroute("**/api/world/feed*")
    finally:
        ctx.close()


def wait_money(page, expected):
    page.wait_for_function("""(expected) => {
      const node = document.getElementById('stMoney');
      return !!node && node.textContent.indexOf(String(expected)) >= 0;
    }""", arg=expected, timeout=10000)


def run_reasons(browser, base, results):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    wait_app(page)
    known = set()
    for src_path, marker in ((SESSION_PY, "REASON_TEXT: dict[str, str] = {"),
                             (AUTH_PY, "ERROR_TEXT: dict[str, str] = {")):
        if not src_path.exists():
            continue
        block = src_path.read_text(encoding="utf-8").split(marker, 1)[1].split("}", 1)[0]
        known |= set(re.findall(r'"([^"]+)":', block))
    required = ["quest_expired", "reputation_too_low", "too_many_quests", "not_together",
                "item_not_held", "auth_required", "auth_bad_username", "auth_bad_password",
                "auth_username_taken", "auth_invalid_credentials", "auth_missing_token",
                "auth_invalid_token", "auth_expired_token", "auth_disabled",
                "intent_not_found"]
    known = sorted(known | set(required))
    mapping = page.evaluate("""(codes) => {
      const x = window.__XKX;
      const rows = {};
      const missing = [];
      codes.forEach(c => {
        const text = x.reasonText(c);
        rows[c] = text;
        if (!text || text.indexOf('服务端没有给出中文说明') >= 0) missing.push(c);
      });
      return {rows, missing, exposed: x.reasons,
              unknown: x.reasonText('zzz_unknown_9'),
              unknownWithServerText: x.reasonText('zzz_unknown_9', '服务端原话')};
    }""", known)
    results.must("原因码：服务端已知码全部有中文文案", not mapping["missing"], mapping["missing"])
    results.must("原因码：前端映射表覆盖已知码",
                 set(known).issubset(set(mapping["exposed"])))
    results.must("原因码：未知码走兜底文案", "服务端没有给出中文说明" in mapping["unknown"])
    results.must("原因码：未知码但有服务端原话时保留原话",
                 mapping["unknownWithServerText"] == "服务端原话")

    # 错误提示：右下角历史最多 3 条、可展开、toast 自动消失
    page.evaluate("() => window.__XKX.showError('quest_expired', '')")
    page.wait_for_function("() => document.getElementById('errorCount').textContent === '1'")
    page.evaluate("() => window.__XKX.showError('reputation_too_low', '')")
    page.evaluate("() => window.__XKX.showError('too_many_quests', '')")
    page.evaluate("() => window.__XKX.showError('not_together', '')")
    hist = page.evaluate("""() => {
      const count = document.getElementById('errorCount').textContent;
      const items = Array.from(document.querySelectorAll('#errorList .error-item')).map(n => n.textContent);
      const toggle = document.getElementById('errorToggle');
      return {count, items, toggleHidden: toggle.classList.contains('hidden'), expanded: toggle.getAttribute('aria-expanded')};
    }""")
    results.must("错误历史：最多保留 3 条", hist["count"] == "3", hist["count"])
    page.click("#errorToggle")
    opened = page.evaluate("""() => ({
      visible: !document.getElementById('errorList').classList.contains('hidden'),
      items: Array.from(document.querySelectorAll('#errorList .error-item')).map(n => n.textContent),
      expanded: document.getElementById('errorToggle').getAttribute('aria-expanded')
    })""")
    results.must("错误历史：可展开复查", opened["visible"] and len(opened["items"]) == 3, opened)
    results.must("错误历史：显示中文原因文案",
                 any("声望还不够" in x for x in opened["items"]),
                 opened["items"])
    time.sleep(5.6)
    toast_hidden = page.evaluate("() => document.getElementById('toast').classList.contains('hidden')")
    results.must("错误 toast 自动消失（不长期遮挡）", toast_hidden)
    capture(page, "reasons-history", results,
            {"failed_check": []})
    ctx.close()


def run_desktop(browser, base, results):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    enter_world(page, "u_guide_desktop")
    page.wait_for_function("() => window.__XKX.guideState().active === true", timeout=8000)

    steps = []
    page.wait_for_timeout(300)
    s1 = guide_snapshot(page)
    results.must("桌面 引导①出现并高亮「先做这个」",
                 s1["visible"] and s1["stepText"].strip() == "1 / 4" and s1["target"]["id"] == "todayNext",
                 {"step": s1["stepText"], "title": s1["title"], "target": s1["target"]})
    results.must("桌面 引导①不遮挡高亮目标", not s1["cardOverlapsTarget"])
    steps.append(capture(page, "desktop-step1", results, {"snapshot": s1}))

    page.click("#guideNext")
    page.wait_for_function("() => window.__XKX.guideState().step === 2")
    page.wait_for_timeout(300)
    s2 = guide_snapshot(page)
    results.must("桌面 引导②出现并高亮「去接」",
                 s2["visible"] and s2["stepText"].strip() == "2 / 4" and bool(s2["target"]["quest"]),
                 {"title": s2["title"], "target": s2["target"]})
    results.must("桌面 引导②不遮挡高亮目标", not s2["cardOverlapsTarget"])
    steps.append(capture(page, "desktop-step2", results, {"snapshot": s2}))

    click_guide_target(page, s2)
    page.wait_for_function("() => window.__XKX.guideState().step === 3", timeout=10000)
    page.wait_for_timeout(300)
    s3 = guide_snapshot(page)
    results.must("桌面 引导③出现并指向移动出口",
                 s3["visible"] and s3["stepText"].strip() == "3 / 4" and bool(s3["target"]["exitId"]),
                 {"title": s3["title"], "target": s3["target"]})
    steps.append(capture(page, "desktop-step3", results, {"snapshot": s3}))

    click_guide_target(page, s3)
    page.wait_for_function("() => window.__XKX.guideState().step === 4", timeout=10000)
    page.wait_for_timeout(300)
    s4 = guide_snapshot(page)
    results.must("桌面 引导④出现并高亮「交付」",
                 s4["visible"] and s4["stepText"].strip() == "4 / 4" and bool(s4["target"]["deliver"]),
                 {"title": s4["title"], "target": s4["target"]})
    steps.append(capture(page, "desktop-step4", results, {"snapshot": s4}))

    click_guide_target(page, s4)
    page.wait_for_function("() => window.__XKX.guideState().status === 'completed'", timeout=10000)
    wait_money(page, 312)
    done = page.evaluate("""() => ({
      guide: window.__XKX.guideState(),
      hidden: document.getElementById('guideLayer').classList.contains('hidden'),
      money: (document.getElementById('stMoney') || {}).textContent,
      persisted: JSON.parse(localStorage.getItem('ws_player_guide_v1') || '{}'),
      localKeys: Object.keys(localStorage)
    })""")
    results.must("桌面 ④交付后资金 300→312 且引导完成",
                 done["guide"]["status"] == "completed" and "312" in (done["money"] or ""), done)
    results.must("桌面 已完成状态写入 localStorage",
                 any(v.get("status") == "completed" for v in done["persisted"].values()), done["persisted"])
    capture(page, "desktop-complete", results, {"snapshot": done})

    # 刷新后不再出现
    page.reload()
    page.wait_for_function("() => !!window.__XKX", timeout=10000)
    page.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                           timeout=8000)
    time.sleep(1.0)
    after = page.evaluate("""() => ({
      guideActive: window.__XKX.guideState().active,
      layerHidden: document.getElementById('guideLayer').classList.contains('hidden'),
      status: window.__XKX.guideState().status
    })""")
    results.must("桌面 刷新后已完成引导不再出现", not after["guideActive"] and after["layerHidden"], after)
    capture(page, "desktop-reload-no-guide", results, {"snapshot": after})
    ctx.close()

    # 跳过流程（另一个账号）
    ctx2 = browser.new_context(viewport={"width": 1280, "height": 900})
    page2 = ctx2.new_page()
    page2.goto(base + "/?ui_check=1")
    enter_world(page2, "u_skip_desktop", "跳过客")
    page2.wait_for_function("() => window.__XKX.guideState().step === 1")
    before = guide_snapshot(page2)
    results.must("桌面 跳过前引导可见", before["visible"])
    page2.click("#guideSkip")
    page2.wait_for_function("() => document.getElementById('guideLayer').classList.contains('hidden')")
    skipped = page2.evaluate("""() => ({
      state: window.__XKX.guideState(),
      persisted: JSON.parse(localStorage.getItem('ws_player_guide_v1') || '{}')
    })""")
    results.must("桌面 可随时跳过并持久化 skipped",
                 skipped["state"]["status"] == "skipped" and
                 any(v.get("status") == "skipped" for v in skipped["persisted"].values()), skipped)
    page2.reload()
    page2.wait_for_function("() => !!window.__XKX", timeout=10000)
    page2.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                            timeout=8000)
    time.sleep(1.0)
    after_skip = page2.evaluate("() => ({active: window.__XKX.guideState().active, hidden: document.getElementById('guideLayer').classList.contains('hidden')})")
    results.must("桌面 刷新后已跳过引导不再出现", not after_skip["active"] and after_skip["hidden"], after_skip)
    ctx2.close()


def run_mobile(browser, base, results):
    ctx = browser.new_context(viewport={"width": 375, "height": 812},
                              device_scale_factor=2, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    enter_world(page, "u_guide_mobile", "手机客")
    page.wait_for_function("() => window.__XKX.guideState().active === true", timeout=8000)

    steps = []
    for step in (1, 2, 3, 4):
        if step > 1:
            page.wait_for_function("(step) => window.__XKX.guideState().step === step", arg=step,
                                   timeout=10000)
        page.wait_for_timeout(300)
        snap = guide_snapshot(page)
        t = snap["target"]
        in_view = bool(t and t["rect"]["left"] >= 0 and t["rect"]["right"] <= 375 and
                       t["rect"]["top"] >= 0 and t["rect"]["bottom"] <= 812)
        ok_button = snap["target"]["tag"] != "BUTTON" or snap["target"]["rect"]["height"] >= 44
        hit_ok = bool(snap["hit"] and snap["hit"]["same"])
        no_overflow = snap["scrollWidth"] <= 375
        small = [x for x in snap["visibleButtons"] if x["h"] < 44]
        results.must("移动 375×812 引导%s目标在视口内" % step, in_view, t)
        results.must("移动 引导%s目标可被点中（未被提示遮挡）" % step, hit_ok, snap["hit"])
        results.must("移动 引导%s触控目标≥44px" % step, ok_button,
                     {"tag": snap["target"]["tag"], "height": t["rect"]["height"]})
        results.must("移动 引导%s无横向溢出" % step, no_overflow, snap["scrollWidth"])
        results.must("移动 引导%s可见按钮均≥44px" % step, not small, small)
        results.must("移动 引导%s提示卡不遮挡高亮目标" % step, not snap["cardOverlapsTarget"])
        entry = capture(page, "mobile-step%d" % step, results, {
            "step": step, "snapshot": snap,
            "viewport": {"width": 375, "height": 812},
            "targetInViewport": in_view, "targetHit": hit_ok,
            "targetHeight": t["rect"]["height"], "horizontalOverflow": snap["scrollWidth"] > 375
        })
        steps.append(entry)
        if step == 1:
            page.click("#guideNext")
        else:
            click_guide_target(page, snap)

    page.wait_for_function("() => window.__XKX.guideState().status === 'completed'", timeout=10000)
    wait_money(page, 312)
    done = page.evaluate("""() => ({
      guide: window.__XKX.guideState(),
      money: (document.getElementById('stMoney') || {}).textContent,
      groups: document.querySelectorAll('#todayList .today-group').length,
      growth: document.querySelectorAll('#growthList .growth-row').length,
      quests: document.querySelectorAll('#questList .quest').length,
      scrollWidth: document.documentElement.scrollWidth
    })""")
    results.must("移动 主流程完成：资金 300→312", "312" in (done["money"] or ""), done)
    results.must("移动 面板四组与三条成长线仍渲染",
                 done["groups"] >= 4 and done["growth"] == 3, done)
    results.must("移动 无横向滚动", done["scrollWidth"] <= 375, done["scrollWidth"])
    capture(page, "mobile-complete", results, {"snapshot": done})

    # 世界动态：移动端默认可见、固定高度、不挤压主线、不被底部浮层遮挡
    page.wait_for_function("() => window.__XKX.feedState().rows >= 5", timeout=10000)
    feed = feed_snapshot(page)
    page.evaluate("""() => document.getElementById('worldFeed')
        .scrollIntoView({block: 'center'})""")
    page.wait_for_timeout(150)
    hit = page.evaluate("""() => {
      const l = document.getElementById('feedList');
      const r = l.getBoundingClientRect();
      const n = document.elementFromPoint(r.left + r.width / 2, r.top + 20);
      return {inside: !!(n && n.closest('#worldFeed')), bottom: Math.round(r.bottom),
              innerHeight: window.innerHeight, scrollWidth: document.documentElement.scrollWidth,
              quests: document.querySelectorAll('#questList .quest').length};
    }""")
    results.add("移动 世界动态默认可见且固定高度（≤200px）",
                 feed["blockVisible"] and feed["count"] >= 5 and feed["height"] <= 200, feed)
    results.add("移动 世界动态不被底部安全区/浮层遮挡",
                 hit["inside"] and hit["bottom"] <= hit["innerHeight"], hit)
    results.add("移动 世界动态无横向溢出、主线任务仍渲染",
                 hit["scrollWidth"] <= 375 and done["quests"] >= 0, hit)
    feed_capture(page, "feed-mobile", results, {"feed": feed, "hit": hit})

    EVIDENCE_DIR.joinpath("mobile-summary.json").write_text(
        json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.close()


def _active_info(page):
    return page.evaluate("""() => {
      const a = document.activeElement;
      if (!a) return null;
      const s = getComputedStyle(a);
      return {tag:a.tagName, id:a.id||'', text:(a.textContent||'').trim().slice(0,24),
              quest:a.getAttribute('data-quest')||'', exitId:a.getAttribute('data-exit-id')||'',
              deliver:a.getAttribute('data-deliver')||'',
              outlineStyle:s.outlineStyle, outlineWidth:s.outlineWidth,
              outlineColor:s.outlineColor};
    }""")


def tab_until(page, predicate, max_tabs=90, trail=None):
    for _ in range(max_tabs):
        page.keyboard.press("Tab")
        info = _active_info(page)
        if trail is not None and info:
            trail.append(info)
        if predicate(info):
            return info
    raise AssertionError("Tab 未能到达目标")


def run_keyboard(browser, base, results):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    enter_world(page, "u_keyboard", "键盘客")
    page.wait_for_function("() => window.__XKX.guideState().step === 1")
    trail = []

    accept = tab_until(page, lambda i: i and i.get("quest"), trail=trail)
    results.must("键盘 Tab 到达「去接」并显示焦点样式",
                 accept and accept["outlineStyle"] != "none" and float(accept["outlineWidth"].rstrip("px")) >= 2,
                 accept)
    page.keyboard.press("Enter")
    page.wait_for_function("() => window.__XKX.guideState().step === 3", timeout=10000)

    page.evaluate("() => document.activeElement && document.activeElement.blur()")
    move = tab_until(page, lambda i: i and i.get("exitId") == "pharmacy", trail=trail)
    results.must("键盘 Tab 到达移动出口并显示焦点样式",
                 move and move["outlineStyle"] != "none", move)
    page.keyboard.press("Enter")
    page.wait_for_function("() => window.__XKX.guideState().step === 4", timeout=10000)

    page.evaluate("() => document.activeElement && document.activeElement.blur()")
    deliver = tab_until(page, lambda i: i and i.get("deliver"), trail=trail)
    results.must("键盘 Tab 到达「交付」并显示焦点样式",
                 deliver and deliver["outlineStyle"] != "none", deliver)
    page.keyboard.press("Enter")
    page.wait_for_function("() => window.__XKX.guideState().status === 'completed'", timeout=10000)
    wait_money(page, 312)
    results.must("键盘 Enter 走完接单→移动→交付", True,
                 {"tabs": len(trail), "focusSequence": [
                     (x.get("id") or x.get("quest") or x.get("exitId") or x.get("deliver") or x.get("text"))
                     for x in trail[-18:]]})
    (EVIDENCE_DIR / "keyboard.txt").write_text(
        "\n".join("%s\t%s\toutline=%s %s" % (x.get("tag"), x.get("id") or x.get("quest") or x.get("exitId") or x.get("deliver") or x.get("text"), x.get("outlineStyle"), x.get("outlineWidth")) for x in trail),
        encoding="utf-8")
    capture(page, "keyboard-complete", results, {"focusTrail": trail})
    ctx.close()


def run_regression(browser, base, results):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    enter_world(page, "u_regression", "回归客")
    page.wait_for_function("() => window.__XKX.guideState().active === true")
    page.click("#guideSkip")
    page.wait_for_function("() => document.getElementById('guideLayer').classList.contains('hidden')")
    dom = page.evaluate("""() => ({
      groups: Array.from(document.querySelectorAll('#todayList .today-group')).map(n => n.textContent.trim().slice(0,8)),
      growth: Array.from(document.querySelectorAll('#growthList .growth-row')).map(n => n.textContent.trim().slice(0,12)),
      quests: document.querySelectorAll('#questList .quest').length,
      noticeHidden: document.getElementById('noticeBar').classList.contains('hidden'),
      authMode: document.getElementById('authBtn').textContent,
      guideHidden: document.getElementById('guideLayer').classList.contains('hidden')
    })""")
    labels = dom["groups"]
    results.must("回归 今日面板四组仍在",
                 all(any(x.startswith(y) for x in labels) for y in ("可接委托", "可学技能", "可拜访 NPC", "可买产业")),
                 labels)
    results.must("回归 三条成长线仍在", len(dom["growth"]) == 3, dom["growth"])
    results.must("回归 委托板仍在", dom["quests"] >= 1, dom["quests"])
    results.must("回归 健康模式下断线提示条不显示", dom["noticeHidden"])
    results.must("回归 legacy 模式显示匿名且不强制登录", dom["authMode"] == "匿名模式", dom["authMode"])
    capture(page, "regression-healthy", results, {"snapshot": dom})
    ctx.close()

    # degraded 独立 mock
    world = MockWorld(upstream_ok=False)
    httpd, dbase = start_server(world)
    try:
        ctx2 = browser.new_context(viewport={"width": 1280, "height": 900})
        page2 = ctx2.new_page()
        page2.goto(dbase + "/?ui_check=1")
        enter_world(page2, "u_degraded", "断线客")
        page2.wait_for_function("() => !document.getElementById('noticeBar').classList.contains('hidden')",
                                timeout=8000)
        notice = page2.evaluate("() => document.getElementById('noticeBar').textContent.trim()")
        results.must("回归 断线提示条仍显示「上游不可达」", "上游不可达" in notice, notice)
        capture(page2, "regression-degraded", results, {"notice": notice})
        ctx2.close()
    finally:
        httpd.shutdown()


def auth_register(page, username="ui_user", password="secret123"):
    page.click("#authTabRegister")
    page.fill("#authUsername", username)
    page.fill("#authPassword", password)
    page.click("#authSubmit")
    page.wait_for_function("() => document.getElementById('authModal').classList.contains('hidden')",
                           timeout=10000)


def auth_login(page, username="ui_user", password="secret123"):
    page.click("#authBtn")
    page.click("#authTabLogin")
    page.fill("#authUsername", username)
    page.fill("#authPassword", password)
    page.click("#authSubmit")
    page.wait_for_function("() => document.getElementById('authModal').classList.contains('hidden')",
                           timeout=10000)


def run_auth(browser, base, results):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    wait_app(page)
    page.wait_for_function("() => window.__XKX.authState().mode === 'auth'", timeout=8000)
    mode = page.evaluate("""() => ({auth: window.__XKX.authState(), btn: document.getElementById('authBtn').textContent,
      entryLine: !document.getElementById('entryAuthLine').classList.contains('hidden')})""")
    results.must("鉴权 未登录显示登录/注册入口",
                 mode["btn"] == "登录/注册" and mode["entryLine"], mode)

    # 未登录进入世界：401 → 自动给出登录入口，不静默
    page.fill("#accountId", "u_auth")
    page.fill("#displayName", "鉴权客")
    page.click("#enterBtn")
    page.wait_for_function("() => !document.getElementById('authModal').classList.contains('hidden')",
                           timeout=8000)
    denied = page.evaluate("""() => ({
      entryError: document.getElementById('entryError').textContent,
      noticeVisible: !document.getElementById('authNotice').classList.contains('hidden'),
      noticeText: document.getElementById('authNotice').textContent,
      modalVisible: !document.getElementById('authModal').classList.contains('hidden')
    })""")
    results.must("鉴权 未登录写操作显示「需要重新登录」入口",
                 "需要登录" in denied["entryError"] and denied["noticeVisible"] and denied["modalVisible"],
                 denied)
    capture(page, "auth-required", results, {"snapshot": denied})

    auth_register(page, "ui_user", "secret123")
    stored = page.evaluate("""() => ({
      auth: window.__XKX.authState(),
      raw: localStorage.getItem('ws_auth_v1'),
      hasPassword: (localStorage.getItem('ws_auth_v1') || '').toLowerCase().indexOf('password') >= 0,
      btn: document.getElementById('authBtn').textContent
    })""")
    results.must("鉴权 注册成功后令牌写入 localStorage 且无口令",
                 stored["auth"]["hasAccess"] and stored["raw"] and not stored["hasPassword"], stored)
    results.must("鉴权 登录态显示在顶部", "已登录" in stored["btn"], stored["btn"])

    page.click("#enterBtn")
    page.wait_for_function("() => document.getElementById('enterBtn').getAttribute('data-open') === '1'",
                           timeout=10000)
    page.click("#enterBtn")
    page.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                           timeout=8000)
    page.wait_for_function("() => window.__XKX.guideState().active === true", timeout=8000)
    # 带 Bearer 接单
    page.click("#guideNext")
    page.wait_for_function("() => window.__XKX.guideState().step === 2")
    snap = guide_snapshot(page)
    click_guide_target(page, snap)
    page.wait_for_function("() => window.__XKX.guideState().step === 3", timeout=10000)
    results.must("鉴权 登录后写操作成功（Bearer 已发送）", True)

    # 刷新：已有令牌启动探活（/api/auth/refresh）→ 轮换并保持登录
    page.reload()
    page.wait_for_function("() => !!window.__XKX", timeout=10000)
    page.wait_for_function("() => window.__XKX.authState().mode === 'auth'", timeout=8000)
    time.sleep(1.0)
    reloaded = page.evaluate("() => ({auth: window.__XKX.authState(), btn: document.getElementById('authBtn').textContent})")

    # 令牌失效：启动探活 401 → 清掉本地令牌并回到登录态（不残留假登录）
    page.evaluate("""() => {
      const o = JSON.parse(localStorage.getItem('ws_auth_v1'));
      o.access_token = 'broken_access_token';
      localStorage.setItem('ws_auth_v1', JSON.stringify(o));
    }""")
    page.reload()
    page.wait_for_function("() => !!window.__XKX", timeout=10000)
    page.wait_for_function("() => window.__XKX.authState().mode === 'auth'", timeout=8000)
    page.wait_for_function("() => window.__XKX.authState().hasAccess === false", timeout=8000)
    broken = page.evaluate("""() => ({auth: window.__XKX.authState(), btn: document.getElementById('authBtn').textContent,
      raw: localStorage.getItem('ws_auth_v1')})""")
    results.must("鉴权 刷新后保持登录；失效令牌启动时清掉并回到登录态",
                 reloaded["auth"]["hasAccess"] and "已登录" in reloaded["btn"]
                 and not broken["auth"]["hasAccess"] and broken["raw"] is None, {"reloaded": reloaded, "broken": broken})

    # refresh 轮换 + 单飞：两个并发写请求都 401，只允许一次 refresh；
    # 旧令牌立即失效，两个请求都用新令牌重放成功。
    auth_login(page, "ui_user", "secret123")
    page.request.get(base + "/__check/reset_stats")
    page.request.get(base + "/__check/force401?n=2")
    race = page.evaluate("""async () => {
      const oldToken = JSON.parse(localStorage.getItem('ws_auth_v1')).access_token;
      const x = window.__XKX;
      const [a, b] = await Promise.all([
        x.apiAction('cultivate', {hours: 1}),
        x.apiAction('cultivate', {hours: 1})
      ]);
      const stats = await (await fetch('/__check/stats')).json();
      const newToken = JSON.parse(localStorage.getItem('ws_auth_v1')).access_token;
      return {a, b, stats, oldToken, newToken};
    }""")
    old_probe = page.request.get(base + "/api/auth/me",
                                 headers={"Authorization": "Bearer " + race["oldToken"]})
    results.must("鉴权 refresh 轮换：并发 401 只刷新一次、旧令牌立即失效",
                 bool(race["a"].get("httpOk")) and bool(race["b"].get("httpOk"))
                 and race["stats"]["refresh_calls"] == 1
                 and race["oldToken"] != race["newToken"]
                 and old_probe.status == 401,
                 {"refresh_calls": race["stats"]["refresh_calls"],
                  "rotate_count": race["stats"]["rotate_count"],
                  "old_status": old_probe.status,
                  "newTokenDiffers": race["oldToken"] != race["newToken"]})
    page.wait_for_function("() => window.__XKX.guideState().step === 3", timeout=8000)

    # 会话中途全部令牌失效：写操作 401 → 提示重新登录，但世界状态不被吞
    page.wait_for_function("() => document.getElementById('entryOverlay').classList.contains('hidden')",
                           timeout=8000)
    page.request.get(base + "/__check/expire")
    page.wait_for_function("() => window.__XKX.guideState().step === 3", timeout=8000)
    page.click('button[data-deliver="%s"]' % QUEST["id"])
    page.wait_for_function("() => !document.getElementById('authNotice').classList.contains('hidden')",
                           timeout=8000)
    mid = page.evaluate("""() => ({
      auth: window.__XKX.authState(),
      noticeText: document.getElementById('authNotice').textContent,
      noticeVisible: !document.getElementById('authNotice').classList.contains('hidden'),
      btn: document.getElementById('authBtn').textContent,
      quests: document.querySelectorAll('#questList .quest').length,
      location: document.getElementById('placeName') ? document.getElementById('placeName').textContent : '',
      errorCount: document.getElementById('errorCount').textContent
    })""")
    results.must("鉴权 写操作 401 显示「需要重新登录」且不吞世界状态",
                 mid["noticeVisible"] and "需要重新登录" in mid["noticeText"] and mid["quests"] >= 1
                 and "需要重新登录" in mid["btn"], mid)
    results.must("鉴权 401 进入错误历史可复查", mid["errorCount"] == "1", mid["errorCount"])
    capture(page, "auth-401-mid-session", results, {"snapshot": mid})
    ctx.close()


COMMERCE_EVIDENCE = ROOT / "reports" / "w4-commerce"


def commerce_capture(page, name):
    COMMERCE_EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = COMMERCE_EVIDENCE / (name + ".png")
    page.screenshot(path=str(path))
    return str(path.relative_to(ROOT))


def commerce_page(browser, base, account, display="商户客", viewport=None):
    ctx = browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/?ui_check=1")
    enter_world(page, account, display)
    page.evaluate("""() => { const b = document.getElementById('guideSkip'); if (b) b.click(); }""")
    page.wait_for_function(
        "() => document.getElementById('guideLayer').classList.contains('hidden')", timeout=8000)
    page.wait_for_function("() => !!window.__XKX.commerceState()", timeout=8000)
    return ctx, page


def commerce_snapshot(page):
    return page.evaluate("""() => {
      const money = window.__XKX.commerceState().money;
      const buy = Array.from(document.querySelectorAll('#buyList .commerce-row')).map(r => ({
        item: r.getAttribute('data-buy'), text: r.textContent.trim(),
        qty: !!r.querySelector('input[data-qty]'), button: !!r.querySelector('[data-buy-go]')
      }));
      const sell = Array.from(document.querySelectorAll('#sellList .commerce-row')).map(r => ({
        item: r.getAttribute('data-sell'), text: r.textContent.trim(),
        button: r.querySelector('[data-sell-go]') ? !r.querySelector('[data-sell-go]').disabled : false
      }));
      const tiers = Array.from(document.querySelectorAll('#tierList .commerce-row')).map(r => ({
        kind: r.getAttribute('data-tier-row'), text: r.textContent.trim(),
        disabled: r.querySelector('[data-tier]') ? r.querySelector('[data-tier]').disabled : null
      }));
      const hires = Array.from(document.querySelectorAll('#hireList .commerce-row')).map(r => ({
        id: r.getAttribute('data-hire-row'), text: r.textContent.trim(),
        pos: r.querySelector('select') ? r.querySelector('select').value : null,
        disabled: r.querySelector('[data-hire]') ? r.querySelector('[data-hire]').disabled : null
      }));
      return {
        money, buy, sell, tiers, hires,
        commerceVisible: !!document.getElementById('commerceBlock'),
        jumpVisible: !!document.getElementById('commerceJump'),
        own: window.__XKX.commerceState().ownShop,
        ownText: (document.getElementById('ownShopInfo') || {}).textContent || '',
        employeeText: (document.getElementById('employeeList') || {}).textContent || '',
        consumeText: (document.getElementById('consumeResult') || {}).textContent || '',
        escortEstimate: (document.getElementById('escortEstimate') || {}).textContent || '',
        escortResult: (document.getElementById('escortResult') || {}).textContent || '',
        hireHint: (document.getElementById('hireHint') || {}).textContent || '',
        soon: (document.getElementById('soonList') || {}).textContent || '',
        errors: Array.from(document.querySelectorAll('#errorList .error-item')).map(e => e.textContent.trim()),
        log: Array.from(document.querySelectorAll('#log .log-item')).map(e => e.textContent.trim())
      };
    }""")


MARRIAGE_EVIDENCE = ROOT / "reports" / "w4-marriage"


def marriage_capture(page, name, results, extra=None):
    MARRIAGE_EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = MARRIAGE_EVIDENCE / (name + ".png")
    page.screenshot(path=str(path))
    if extra is not None:
        extra["screenshot"] = str(path.relative_to(ROOT))
    return extra


def marriage_snapshot(page):
    return page.evaluate("() => window.__XKX.marriageState()")


def marriage_set_relation(page, account, target, aff, trust):
    page.evaluate("""async (p) => {
      const url = '/__check/marriage?account=' + encodeURIComponent(p.account) +
        '&target=' + encodeURIComponent(p.target) +
        '&affection=' + encodeURIComponent(p.aff) + '&trust=' + encodeURIComponent(p.trust);
      await fetch(url);
      await window.__XKX.refreshState(p.account);
    }""", {"account": account, "target": target, "aff": aff, "trust": trust})
    page.wait_for_function("""(p) => {
      const s = window.__XKX.marriageState();
      const c = (s.candidates || []).filter(x => x.id === p.target)[0];
      return !!c && Math.abs(c.affection - p.aff) < 0.01 && Math.abs(c.trust - p.trust) < 0.01;
    }""", arg={"target": target, "aff": aff, "trust": trust}, timeout=8000)


def run_marriage(browser, base, results, world):
    account = "u_marriage_mock"
    spouse_id = "npc:wang_tiejiang"
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    shots = []

    def shot(name, extra=None):
        shots.append(name)
        return marriage_capture(page, name, results, extra)

    def choose(target):
        page.click('[data-propose-choose="%s"]' % target)
        page.wait_for_function("(id) => window.__XKX.marriageState().target === id",
                               arg=target, timeout=5000)

    def propose(money, item, qty):
        page.fill("#proposeMoney", str(money))
        page.select_option("#proposeItem", item)
        page.fill("#proposeQty", str(qty))
        page.click("#proposeGo")

    def wait_result(needle, timeout=12000):
        page.wait_for_function(
            "(needle) => document.getElementById('proposeResult').textContent.indexOf(needle) >= 0",
            arg=needle, timeout=timeout)

    try:
        page.goto(base + "/?ui_check=1")
        enter_world(page, account, "婚仪验客")
        try:
            page.wait_for_selector("#guideSkip", state="visible", timeout=4000)
            page.click("#guideSkip")
            page.wait_for_function("() => document.getElementById('guideLayer').classList.contains('hidden')",
                                   timeout=6000)
        except Exception:
            pass
        page.locator("#commerceBlock").scroll_into_view_if_needed()
        page.click('button[data-tab="marriage"]')
        page.wait_for_function("() => !document.getElementById('pane-marriage').classList.contains('hidden')")
        snap = marriage_snapshot(page)
        rows = snap["candidates"]
        results.add("婚姻 候选列表（好感/信任/已婚/同地点）",
                    len(rows) >= 3 and all(row["married"] for row in rows),
                    {"candidates": rows})
        list_text = page.locator("#proposeList").inner_text()
        results.add("婚姻 候选行显示四项字段",
                    "好感" in list_text and "信任" in list_text
                    and "同地点" in list_text and "已婚状态" in list_text,
                    {"text": list_text[:160]})
        wang = [row for row in rows if row["id"] == spouse_id][0]
        results.add("婚姻 候选数据（王铁匠 7.0/7.0）",
                    abs(wang["affection"] - 7.0) < 0.01 and abs(wang["trust"] - 7.0) < 0.01
                    and wang["sameLocation"] and not wang["isSpouse"],
                    wang)
        shot("mock-marriage-1-candidates")

        # 1) 好感/信任不足：中文婉拒，不露 code
        choose(spouse_id)
        marriage_set_relation(page, account, spouse_id, 10, 10)
        page.fill("#proposeMoney", "0")
        page.select_option("#proposeItem", "")
        page.click("#proposeGo")
        wait_result("婉拒")
        text = page.locator("#proposeResult").inner_text()
        results.add("婚姻 婉拒为中文且不露 code",
                    "婉拒" in text and "bond_too_low" not in text and "trust_too_low" not in text,
                    {"result": text})
        shot("mock-marriage-2-reject")

        # 2) 原因码映射全表（婚姻 + 后续 rest/押镖码）
        codes = ["bond_too_low", "trust_too_low", "gift_missing", "not_enough_money",
                 "already_married", "target_in_couple", "not_together", "self_propose",
                 "spouse_not_here", "not_married", "no_payer_funds",
                 "cannot_rest_while_escorting", "cannot_rest_now"]
        mapped = page.evaluate("""(codes) => codes.map(c => [c, window.__XKX.reasonText(c)])""",
                               codes)
        bad = [c for c, t in mapped if not t or c in t]
        results.add("婚姻 13 个原因码均有中文映射",
                    not bad and all(len(t) >= 6 for _, t in mapped),
                    {"mapped": mapped, "bad": bad})

        # 3) 再处处看：不扣聘礼
        before = marriage_snapshot(page)["money"]
        marriage_set_relation(page, account, spouse_id, 50, 25)
        propose(20, "", 1)
        wait_result("再处处看")
        after = marriage_snapshot(page)["money"]
        results.add("婚姻 再处处看不扣聘礼",
                    after == before and "再处处看" in page.locator("#proposeResult").inner_text(),
                    {"before": before, "after": after})
        shot("mock-marriage-3-courtship")

        # 4) 达到门槛：成婚 + 配偶栏 + 聘礼/信物扣除
        marriage_set_relation(page, account, spouse_id, 65, 35)
        gift = [x for x in marriage_snapshot(page)["giftRows"] if x == "coarse_cloth"]
        propose(40, "coarse_cloth", 1)
        page.wait_for_function("() => window.__XKX.marriageState().status === 'married'",
                               timeout=12000)
        snap = marriage_snapshot(page)
        results.add("婚姻 成婚结果含姓名与 tick",
                    "成婚，tick" in snap["result"] and "王铁匠" in snap["result"],
                    {"result": snap["result"]})
        results.add("婚姻 配偶常驻栏出现",
                    snap["spouseBlockVisible"] and "王铁匠" in snap["spouseInfo"]
                    and "成婚 tick" in snap["spouseInfo"],
                    {"spouseInfo": snap["spouseInfo"], "spouseNote": snap["spouseNote"]})
        results.add("婚姻 今日陪伴口径可见",
                    "今日已陪伴" in snap["spouseNote"] or "同地点" in snap["spouseNote"],
                    {"spouseNote": snap["spouseNote"]})
        results.add("婚姻 聘礼与信物真实扣除",
                    snap["money"] == before - 40 and "coarse_cloth" not in snap["giftRows"],
                    {"money": snap["money"], "before": before, "giftRows": snap["giftRows"],
                     "gift_before": gift})
        shot("mock-marriage-4-spouse-block")

        # 5) 配偶对话与普通 NPC 可区分
        page.wait_for_selector(".npc.spouse", timeout=8000)
        page.click(".npc.spouse")
        page.wait_for_function("() => document.getElementById('talkTarget').textContent.indexOf('（配偶）') >= 0",
                               timeout=8000)
        page.click("#npcClose")
        page.wait_for_function("() => document.getElementById('npcModal').classList.contains('hidden')",
                               timeout=6000)
        page.fill("#talkInput", "今天回来吃饭吗")
        page.click("#talkSend")
        page.wait_for_selector(".reply-item.spouse", timeout=12000)
        reply = page.locator(".reply-item.spouse").first.inner_text()
        results.add("婚姻 配偶对话标记（配偶）并高亮",
                    "（配偶）" in page.locator("#talkTarget").inner_text()
                    and "（配偶）" in reply,
                    {"talkTarget": page.locator("#talkTarget").inner_text(), "reply": reply})
        shot("mock-marriage-5-spouse-talk")

        # 6) 和离二次确认：取消不提交、确认后回单身
        page.click("#marriageEndBtn")
        page.wait_for_function("() => !document.getElementById('divorceModal').classList.contains('hidden')")
        modal_text = page.locator("#divorceModal").inner_text()
        results.add("婚姻 和离弹层写清聘礼不退还",
                    "聘礼不退还" in modal_text and "和离" in modal_text,
                    {"modal": modal_text})
        shot("mock-marriage-6-divorce-confirm")
        page.click("#divorceCancel")
        page.wait_for_function("() => document.getElementById('divorceModal').classList.contains('hidden')")
        results.add("婚姻 取消和离不改变婚姻态",
                    marriage_snapshot(page)["status"] == "married",
                    {"status": marriage_snapshot(page)["status"]})
        before_tick = world.accounts[account]["tick"]
        page.click("#marriageEndBtn")
        page.click("#divorceConfirm")
        page.wait_for_function("() => window.__XKX.marriageState().status === 'single'", timeout=12000)
        snap = marriage_snapshot(page)
        results.add("婚姻 和离成功回单身且配偶栏隐藏",
                    not snap["spouseBlockVisible"] and "和离" in snap["result"],
                    {"result": snap["result"], "tick_before": before_tick,
                     "tick_after": world.accounts[account]["tick"]})
        shot("mock-marriage-7-single")

        # 7) 未开放页只留拜师（立派/收徒已在「门派」页签开放）
        page.click('button[data-tab="soon"]')
        page.wait_for_function("() => !document.getElementById('pane-soon').classList.contains('hidden')")
        soon = page.locator("#pane-soon").inner_text()
        results.add("婚姻 未开放页不再重复立派/收徒，仅留拜师",
                    "拜师" in soon
                    and all(k not in soon for k in ("结婚", "立派", "收徒")),
                    {"soon": soon})

        # 8) rest 占位：提示但不发请求
        page.click('button[data-tab="shop"]')
        before_tick = world.accounts[account]["tick"]
        page.click("#restBtn")
        rest = page.locator("#restResult").inner_text()
        results.add("婚姻 rest 占位只提示，不发请求",
                    "体力恢复中" in rest and world.accounts[account]["tick"] == before_tick,
                    {"rest": rest, "tick_before": before_tick,
                     "tick_after": world.accounts[account]["tick"]})

        # 9) 375×812 不横向溢出、控件 ≥44px
        page.set_viewport_size({"width": 375, "height": 812})
        page.click('button[data-tab="marriage"]')
        page.wait_for_selector("#proposeGo:visible")
        sizes = page.evaluate("""() => {
          const out = {scrollWidth: document.documentElement.scrollWidth,
                       innerWidth: window.innerWidth,
                       controls: {}};
          for (const sel of ['#proposeGo', '#proposeMoney', '#proposeItem',
                             '#proposeQty']) {
            const n = document.querySelector(sel);
            const r = n ? n.getBoundingClientRect() : {height: 0};
            out.controls[sel] = Math.round(r.height);
          }
          return out;
        }""")
        shot("mock-marriage-8-mobile")
        page.click('button[data-tab="shop"]')
        page.wait_for_selector("#restBtn:visible")
        rest_h = page.evaluate("""() => Math.round(document.getElementById('restBtn')
          .getBoundingClientRect().height)""")
        sizes["controls"]["#restBtn"] = rest_h
        results.add("婚姻 375×812 不横向溢出",
                    sizes["scrollWidth"] <= sizes["innerWidth"] + 1, sizes)
        results.add("婚姻 375×812 新增控件 ≥44px",
                    all(h >= 44 for h in sizes["controls"].values()), sizes)

        # 10) 键盘：页签可聚焦、Enter 切换
        page.set_viewport_size({"width": 1280, "height": 900})
        page.focus('button[data-tab="marriage"]')
        focused = page.evaluate("() => document.activeElement && document.activeElement.getAttribute('data-tab')")
        page.keyboard.press("Enter")
        results.add("婚姻 键盘可聚焦并切换页签",
                    focused == "marriage"
                    and not page.locator("#pane-marriage").evaluate(
                        "n => n.classList.contains('hidden')"),
                    {"focused": focused})
    finally:
        ctx.close()
    (MARRIAGE_EVIDENCE / "mock-marriage.json").write_text(
        json.dumps({"shots": shots, "checks": [r for r in results.rows if r["name"].startswith("婚姻 ")],
                    "failed": sum(1 for r in results.rows if r["name"].startswith("婚姻 ") and not r["ok"])},
                   ensure_ascii=False, indent=2), encoding="utf-8")


def run_commerce(browser, base, results, world):
    ctx, page = commerce_page(browser, base, "u_commerce", "商户甲")
    snap = commerce_snapshot(page)
    results.must("商业 入口可见（顶栏按钮 + 面板）", snap["jumpVisible"] and snap["commerceVisible"], snap)
    results.must("商业 商店页列出可买物品（单价/含税/库存/限购）",
                 any(r["item"] == "flatbread" and "炊饼" in r["text"] and "单价 3" in r["text"]
                     and "含税 3" in r["text"] and "库存 6" in r["text"] and "限购" in r["text"]
                     and r["qty"] and r["button"] for r in snap["buy"]), snap["buy"])
    results.must("商业 可卖物品显示 sell_price 与持有数量",
                 any(r["item"] == "coarse_cloth" and "售价 15" in r["text"] and "持有 1" in r["text"]
                     for r in snap["sell"]), snap["sell"])

    page.fill('input[data-qty="buy:flatbread"]', "2")
    page.click('[data-buy-go="flatbread"]')
    page.wait_for_function("() => window.__XKX.commerceState().money === 294", timeout=8000)
    snap = commerce_snapshot(page)
    results.must("商业 buy 数量 2 真调用（钱包 300→294）",
                 "库存 4" in next(r["text"] for r in snap["buy"] if r["item"] == "flatbread")
                 and any("买" in row for row in snap["log"]), snap["buy"])
    commerce_capture(page, "mock-commerce-buy")

    page.click("#consumeBtn")
    page.wait_for_function("() => window.__XKX.commerceState().money === 291", timeout=8000)
    page.wait_for_function("() => document.getElementById('consumeResult').textContent.indexOf('体力 82 → 100') >= 0",
                           timeout=8000)
    snap = commerce_snapshot(page)
    results.must("商业 consume 一键现吃并显示体力变化",
                 "体力 82 → 100（+18）" in snap["consumeText"] and "钱包 294 → 291" in snap["consumeText"],
                 snap["consumeText"])
    commerce_capture(page, "mock-commerce-consume")

    page.fill('input[data-qty="sell:coarse_cloth"]', "1")
    page.click('[data-sell-go="coarse_cloth"]')
    page.wait_for_function("() => window.__XKX.commerceState().money === 306", timeout=8000)
    snap = commerce_snapshot(page)
    results.must("商业 sell 数量 1 真调用（钱包 291→306）",
                 any("卖" in row for row in snap["log"]) and
                 all(not r["button"] for r in snap["sell"] if r["item"] == "coarse_cloth"), snap["sell"])

    # 8 秒刷新：tick 推进后库存变化必须在下一轮轮询内进入界面。
    started = time.monotonic()
    page.request.get(base + "/__check/tick?account=u_commerce&n=6&stock=1")
    page.wait_for_function(
        "() => /库存 3/.test(document.querySelector('[data-buy=flatbread]').textContent)",
        timeout=12000)
    elapsed = time.monotonic() - started
    results.must("商业 价格/库存 8 秒内刷新（无输入时）", elapsed <= 9.5, {"elapsed": round(elapsed, 2)})

    # 正在输入的数量在轮询期间不被重建/清空。
    page.focus('input[data-qty="buy:flatbread"]')
    page.fill('input[data-qty="buy:flatbread"]', "7")
    page.request.get(base + "/__check/tick?account=u_commerce&n=6&stock=1")
    page.wait_for_timeout(9000)
    typing = page.evaluate("""() => {
      const el = document.querySelector('input[data-qty="buy:flatbread"]');
      return {value: el.value, focused: document.activeElement === el,
              deferred: window.__XKX.commerceState().deferred};
    }""")
    results.must("商业 刷新不打断正在输入的数量", typing["value"] == "7" and typing["focused"],
                 typing)
    page.evaluate("() => document.activeElement.blur()")
    page.wait_for_function(
        "() => /库存 2/.test(document.querySelector('[data-buy=flatbread]').textContent)", timeout=4000)

    page.click('[data-tab="business"]')
    snap = commerce_snapshot(page)
    stall = next(r for r in snap["tiers"] if r["kind"] == "stall")
    results.must("商业 资金不足时开店按钮禁用并给原因",
                 stall["disabled"] and "还差" in stall["text"] and "共需 345" in stall["text"], stall)
    page.request.get(base + "/__check/money?account=u_commerce&value=2000")
    page.evaluate("() => window.__XKX.refreshState('u_commerce')")
    page.wait_for_function("() => window.__XKX.commerceState().money === 2000", timeout=8000)
    page.click('[data-tier="storefront"]')
    page.wait_for_function("() => !!window.__XKX.commerceState().ownShop", timeout=8000)
    page.wait_for_function("() => window.__XKX.commerceState().money === 528", timeout=8000)
    snap = commerce_snapshot(page)
    results.must("商业 open_shop 三档开店成功并显示店内现金/库存",
                 snap["own"] and snap["own"]["kind"] == "storefront" and
                 "店内现金" in snap["ownText"] and "库存" in snap["ownText"], snap["ownText"])
    results.must("商业 已有店铺后三档按钮禁用并给原因",
                 all(r["disabled"] and "已经有店铺" in r["text"] for r in snap["tiers"]), snap["tiers"])
    commerce_capture(page, "mock-commerce-open-shop")

    page.click('[data-tab="hire"]')
    snap = commerce_snapshot(page)
    wang = next(r for r in snap["hires"] if r["id"] == "npc:wang_tiejiang")
    liu = next(r for r in snap["hires"] if r["id"] == "npc:liu_sanniang")
    results.must("商业 雇人列表显示好感/技能/受雇状态",
                 "好感 7.0" in wang["text"] and "技能" in wang["text"] and "受雇" in wang["text"], wang)
    results.must("商业 低好感 NPC 明确标出", "好感 0.0（不足 6.0）" in liu["text"], liu)

    page.select_option('select[data-pos-for="npc:wang_tiejiang"]', "铁匠")
    page.fill("#hireWage", "20")
    page.click('[data-hire="npc:wang_tiejiang"]')
    page.wait_for_function(
        "() => document.getElementById('errorList').textContent.indexOf('日薪低于市价') >= 0",
        timeout=8000)
    snap = commerce_snapshot(page)
    results.must("商业 wage_too_low 映射中文且不露原始码",
                 any("日薪低于市价" in e for e in snap["errors"]) and
                 all("wage_too_low" not in e for e in snap["errors"]), snap["errors"])

    page.fill("#hireWage", "32")
    page.click('[data-hire="npc:wang_tiejiang"]')
    page.wait_for_function(
        "() => document.getElementById('employeeList').textContent.indexOf('王铁匠') >= 0", timeout=8000)
    snap = commerce_snapshot(page)
    results.must("商业 recruit 成功并显示岗位/日薪/到店",
                 "王铁匠" in snap["employeeText"] and "铁匠" in snap["employeeText"]
                 and "32" in snap["employeeText"] and "到店" in snap["employeeText"],
                 snap["employeeText"])

    page.select_option('select[data-pos-for="npc:liu_sanniang"]', "脚夫")
    page.fill("#hireWage", "18")
    page.click('[data-hire="npc:liu_sanniang"]')
    page.wait_for_function(
        "() => document.getElementById('errorList').textContent.indexOf('好感不足') >= 0", timeout=8000)
    snap = commerce_snapshot(page)
    results.must("商业 relation_too_low 映射中文", any("好感不足" in e for e in snap["errors"]),
                 snap["errors"])

    page.select_option('select[data-pos-for="npc:chen_huolang"]', "货郎")
    page.fill("#hireWage", "26")
    page.click('[data-hire="npc:chen_huolang"]')
    page.wait_for_function(
        "() => document.getElementById('errorList').textContent.indexOf('手艺还不够') >= 0", timeout=8000)
    snap = commerce_snapshot(page)
    results.must("商业 skill_too_low 映射中文", any("手艺还不够" in e for e in snap["errors"]),
                 snap["errors"])

    page.select_option('select[data-pos-for="npc:afu"]', "药童")
    page.fill("#hireWage", "15")
    page.click('[data-hire="npc:afu"]')
    page.wait_for_function(
        "() => document.getElementById('employeeList').textContent.indexOf('阿福') >= 0", timeout=8000)
    snap = commerce_snapshot(page)
    liu_full = next(r for r in snap["hires"] if r["id"] == "npc:liu_sanniang")
    results.must("商业 shop_full 映射中文（满员按钮禁用并给原因）",
                 liu_full["disabled"] and "已满员" in liu_full["text"]
                 and "雇工已满" in page.evaluate("() => window.__XKX.reasonText('shop_full', '')"),
                 liu_full)
    commerce_capture(page, "mock-commerce-hire")

    page.click('[data-tab="escort"]')
    snap = commerce_snapshot(page)
    results.must("商业 无镖局时押镖入口禁用并说明原因",
                 "这里没有镖局" in snap["escortEstimate"] and
                 page.evaluate("() => document.getElementById('escortGo').disabled"), snap["escortEstimate"])

    page.click('button[data-exit-id="escort_agency"]')
    page.wait_for_function("() => window.__XKX.commerceState().escortDests.indexOf('market') >= 0",
                           timeout=8000)
    page.wait_for_function(
        "() => document.getElementById('escortEstimate').textContent.indexOf('预计到手') >= 0",
        timeout=8000)
    page.select_option("#escortDest", "market")
    page.select_option("#escortCargo", "flatbread")
    page.fill("#escortFee", "180")
    page.fill("#escortQty", "1")
    page.wait_for_function(
        "() => document.getElementById('escortEstimate').textContent.indexOf('预计到手 126 文') >= 0",
        timeout=4000)
    snap = commerce_snapshot(page)
    results.must("商业 押镖显示预计收入与到货 tick",
                 "预计到手 126 文" in snap["escortEstimate"] and
                 "到货 tick" in snap["escortEstimate"], snap["escortEstimate"])
    page.fill("#escortFee", "100")
    page.wait_for_function(
        "() => document.getElementById('escortEstimate').textContent.indexOf('镖费需在 120-260 文之间') >= 0",
        timeout=4000)
    snap = commerce_snapshot(page)
    results.must("商业 镖费越界本地给出中文原因",
                 "镖费需在 120-260 文之间" in snap["escortEstimate"]
                 and page.evaluate("() => document.getElementById('escortGo').disabled"),
                 snap["escortEstimate"])
    page.fill("#escortFee", "180")
    page.click("#escortGo")
    page.wait_for_function(
        "() => document.getElementById('escortResult').textContent.indexOf('镖单已接') >= 0",
        timeout=8000)
    snap = commerce_snapshot(page)
    results.must("商业 escort 真调用并显示到货 tick",
                 "镖单已接" in snap["escortResult"] and "到货 tick" in snap["escortResult"]
                 and "预计到手 126 文" in snap["escortResult"], snap["escortResult"])
    commerce_capture(page, "mock-commerce-escort")

    page.click('[data-tab="soon"]')
    snap = commerce_snapshot(page)
    results.must("商业 未开放玩法只留拜师，立派/收徒已移入「门派」页签",
                 "拜师" in snap["soon"] and "该玩法尚未开放" in snap["soon"]
                 and all(k not in snap["soon"] for k in ("立派", "收徒", "结婚")),
                 snap["soon"])
    results.must("商业 not_implemented_for_player 中文映射",
                 page.evaluate("() => window.__XKX.reasonText('not_implemented_for_player', '')") == "该玩法尚未开放。",
                 page.evaluate("() => window.__XKX.reasonText('not_implemented_for_player', '')"))
    ctx.close()

    # 移动端 375×812：无横向溢出、触控目标 ≥44px。
    mctx, mpage = commerce_page(browser, base, "u_commerce_m", "商户乙",
                                viewport={"width": 375, "height": 812})
    mpage.click("#commerceJump")
    mpage.wait_for_timeout(300)
    mobile_checks = []
    for tab in ("shop", "business", "hire", "escort", "soon"):
        mpage.click('[data-tab="%s"]' % tab)
        mpage.wait_for_timeout(120)
        mobile_checks.append(mpage.evaluate("""() => {
          const controls = Array.from(document.querySelectorAll(
            '#commerceBlock button, #commerceBlock input, #commerceBlock select'))
            .filter(n => n.getBoundingClientRect().height > 0);
          const small = controls.filter(n => n.getBoundingClientRect().height < 44)
            .map(n => ({tag: n.tagName, text: (n.textContent || n.value || '').slice(0, 12),
                        h: Math.round(n.getBoundingClientRect().height)}));
          return {
            tab: window.__XKX.commerceState().tab,
            overflow: document.documentElement.scrollWidth - window.innerWidth,
            small: small, controls: controls.length,
            tabsFit: (() => { const n = document.getElementById('commerceTabs');
              return n.scrollWidth <= n.clientWidth + 1; })()
          };
        }"""))
    results.must("移动 商业 375×812 无横向溢出且触控≥44px",
                 all(c["overflow"] <= 0 and not c["small"] and c["tabsFit"] and c["controls"] >= 1
                     for c in mobile_checks), mobile_checks)
    mpage.click("#commerceJump")
    commerce_capture(mpage, "mock-commerce-mobile")
    mctx.close()

    # 键盘：Tab/方向键/Enter 能切换页签并完成一次购买。
    kctx, kpage = commerce_page(browser, base, "u_commerce_k", "商户丙")
    kpage.evaluate("() => document.getElementById('commerceJump').focus()")
    kpage.keyboard.press("Enter")
    kpage.wait_for_function(
        "() => document.activeElement && document.activeElement.getAttribute('data-tab') === 'shop'",
        timeout=4000)
    kpage.keyboard.press("ArrowRight")
    kpage.wait_for_function(
        "() => document.activeElement && document.activeElement.getAttribute('data-tab') === 'business'",
        timeout=4000)
    kpage.focus('[data-tab="shop"]')
    kpage.keyboard.press("Enter")
    kpage.focus('input[data-qty="buy:flatbread"]')
    kpage.fill('input[data-qty="buy:flatbread"]', "1")
    kpage.keyboard.press("Tab")
    kpage.keyboard.press("Enter")
    kpage.wait_for_function("() => window.__XKX.commerceState().money === 297", timeout=8000)
    results.must("商业 键盘可切换页签并完成购买",
                 kpage.evaluate("() => window.__XKX.commerceState().money") == 297,
                 {"money": 297})
    kctx.close()


SHOPOPS_EVIDENCE = ROOT / "reports" / "w4-shopops"


def shopops_capture(page, name):
    SHOPOPS_EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = SHOPOPS_EVIDENCE / (name + ".png")
    page.screenshot(path=str(path), full_page=True)
    return str(path.relative_to(ROOT))


def shopops_snapshot(page):
    return page.evaluate("""() => {
      const op = window.__XKX.shopOpsState();
      const rows = Array.from(document.querySelectorAll('#ledgerBody tr')).map(r => ({
        day: r.getAttribute('data-ledger-day'), text: r.textContent.trim(),
        reason: (r.querySelector('.ledger-reason') || {}).textContent || '',
        netClass: (r.children[5] || {}).className || '',
        netColor: (r.children[5] ? getComputedStyle(r.children[5]).color : '')
      }));
      return {
        op,
        restockRows: Array.from(document.querySelectorAll('#restockList .commerce-row')).map(r => ({
          item: r.getAttribute('data-restock-row'), text: r.textContent.trim(),
          qty: !!r.querySelector('input[data-restock-qty]'),
          disabled: r.querySelector('[data-restock-go]') ? r.querySelector('[data-restock-go]').disabled : null,
          alerts: Array.from(r.querySelectorAll('.stock-alert')).map(x => x.textContent.trim()),
          fill: r.querySelector('[data-restock-fill]')
            ? {text: r.querySelector('[data-restock-fill]').textContent.trim(),
               qty: r.querySelector('[data-restock-fill]').getAttribute('data-fill-qty')}
            : null
        })),
        priceRows: Array.from(document.querySelectorAll('#priceList .commerce-row')).map(r => ({
          item: r.getAttribute('data-price-row'), text: r.textContent.trim(),
          note: (r.querySelector('[data-price-note]') || {}).textContent || '',
          disabled: r.querySelector('[data-price-go]') ? r.querySelector('[data-price-go]').disabled : null
        })),
        employees: Array.from(document.querySelectorAll('#employeeList .commerce-row')).map(r => ({
          id: r.getAttribute('data-employee'), text: r.textContent.trim(),
          hasDismiss: !!r.querySelector('[data-dismiss]')
        })),
        withdrawDisabled: document.getElementById('withdrawGo').disabled,
        withdrawText: document.getElementById('shopCashNow').textContent.trim(),
        restockTotal: document.getElementById('restockTotal').textContent.trim(),
        ledger: {
          emptyVisible: !document.getElementById('ledgerEmpty').classList.contains('hidden'),
          emptyText: document.getElementById('ledgerEmpty').textContent.trim(),
          wrapVisible: !document.getElementById('ledgerWrap').classList.contains('hidden'),
          rows: rows,
          totalText: document.getElementById('ledgerFoot').textContent.trim(),
          stockoutVisible: !document.getElementById('ledgerStockout').hidden,
          stockoutText: document.getElementById('ledgerStockout').textContent.trim(),
          wrapScroll: document.getElementById('ledgerWrap').scrollWidth,
          wrapClient: document.getElementById('ledgerWrap').clientWidth
        },
        modalVisible: !document.getElementById('dismissModal').classList.contains('hidden'),
        modalText: document.getElementById('dismissModal').textContent.trim(),
        toast: document.getElementById('toast').textContent.trim(),
        errors: Array.from(document.querySelectorAll('#errorList .error-item')).map(e => e.textContent.trim()),
        log: Array.from(document.querySelectorAll('#log .log-item')).map(e => e.textContent.trim())
      };
    }""")


def run_shopops(browser, base, results, world):
    ctx, page = commerce_page(browser, base, "u_shopops", "店掌柜")
    page.click('[data-tab="business"]')
    page.wait_for_timeout(600)
    snap = shopops_snapshot(page)
    results.must("店铺经营 无店时四区禁用并提示开店",
                 "你还没有店铺" in snap["op"]["reason"]
                 and snap["withdrawDisabled"]
                 and all(not r["disabled"] for r in snap["restockRows"])
                 and snap["ledger"]["emptyVisible"]
                 and "你还没有店铺；开张后才有日结账本" in snap["ledger"]["emptyText"]
                 and not snap["ledger"]["stockoutVisible"], snap)
    shopops_capture(page, "mock-shopops-noshop")

    page.request.get(base + "/__check/money?account=u_shopops&value=2000")
    page.evaluate("() => window.__XKX.refreshState('u_shopops')")
    page.wait_for_function("() => window.__XKX.commerceState().money === 2000", timeout=8000)
    page.click('[data-tier="storefront"]')
    page.wait_for_function("() => !!window.__XKX.commerceState().ownShop", timeout=8000)
    page.wait_for_timeout(800)

    page.request.get(base + "/__check/shop_report?account=u_shopops&mode=sample&cash=100")
    page.evaluate("() => { window.__XKX.refreshShopReport(); return window.__XKX.refreshState('u_shopops'); }")
    page.wait_for_function(
        "() => document.querySelectorAll('#ledgerBody tr').length === 7", timeout=8000)
    snap = shopops_snapshot(page)
    results.must("店铺经营 开店后四区启用（进货/定价/辞退/提现）",
                 snap["op"]["reason"] == "" and snap["op"]["cash"] == 100
                 and len(snap["restockRows"]) >= 3 and all(r["qty"] for r in snap["restockRows"])
                 and len(snap["priceRows"]) >= 3 and all(not r["disabled"] for r in snap["priceRows"])
                 and not snap["withdrawDisabled"]
                 and "当前店现 100 文" in snap["withdrawText"], snap)

    results.must("店铺经营 账本 7 行与「7 日合计」行渲染",
                 len(snap["ledger"]["rows"]) == 7
                 and "335" in snap["ledger"]["totalText"] and "198" in snap["ledger"]["totalText"]
                 and "113" in snap["ledger"]["totalText"] and "44" in snap["ledger"]["totalText"]
                 and "7 日合计" in snap["ledger"]["totalText"], snap["ledger"])
    results.must("店铺经营 正负净利着色且负行由数值推导原因",
                 any("profit-pos" in r["netClass"] for r in snap["ledger"]["rows"])
                 and any("profit-neg" in r["netClass"] for r in snap["ledger"]["rows"])
                 and any("当日无客流" in r["reason"] for r in snap["ledger"]["rows"])
                 and any("工资高于营收" in r["reason"] for r in snap["ledger"]["rows"])
                 and any("进货占去营收" in r["reason"] for r in snap["ledger"]["rows"])
                 and len(set(r["netColor"] for r in snap["ledger"]["rows"])) >= 2, snap["ledger"])
    shopops_capture(page, "mock-shopops-ledger")

    # 缺货日：账本用中文直说，库存低/售罄 SKU 给提示与「一键补到建议量」入口。
    page.request.get(base + "/__check/shop_report?account=u_shopops&mode=stockout&cash=80&stock=low")
    page.evaluate("() => { window.__XKX.refreshShopReport(); return window.__XKX.refreshState('u_shopops'); }")
    page.wait_for_function(
        "() => document.querySelectorAll('#ledgerBody tr').length === 2", timeout=8000)
    snap = shopops_snapshot(page)
    flat_row = next(r for r in snap["restockRows"] if r["item"] == "flatbread")
    noodle_row = next(r for r in snap["restockRows"] if r["item"] == "noodle")
    results.must("店铺经营 缺货日在账本里用中文直说（含具体 SKU）",
                 any("缺货导致零成交（炊饼/阳春面）" in r["reason"]
                     for r in snap["ledger"]["rows"]), snap["ledger"]["rows"])
    results.must("店铺经营 账本提示最近缺货天数，空账本不显示",
                 snap["ledger"]["stockoutVisible"]
                 and "有 1 天缺货导致零成交" in snap["ledger"]["stockoutText"], snap["ledger"])
    target = int(flat_row["fill"]["text"].replace("补到 ", "").replace(" 件", ""))
    results.must("店铺经营 低库存/售罄 SKU 有显式提示与建议量入口",
                 target in (10, 15, 20)
                 and "库存不足（低于 3 件）" in flat_row["text"]
                 and flat_row["fill"]["qty"] == str(target - 1)
                 and "已售罄 · 优先补货" in noodle_row["text"]
                 and noodle_row["fill"] == {"text": "补到 %d 件" % target,
                                            "qty": str(target)}, snap["restockRows"])
    calls = page.request.get(base + "/__check/verb_stats?verb=restock").json()["calls"]
    page.click('[data-restock-fill="noodle"]')
    page.wait_for_timeout(400)
    filled = page.input_value('input[data-restock-qty="noodle"]')
    after_calls = page.request.get(base + "/__check/verb_stats?verb=restock").json()["calls"]
    snap = shopops_snapshot(page)
    results.must("店铺经营 一键补货只预填建议量，仍要玩家点「进货」确认",
                 filled == str(target) and calls == after_calls
                 and "已按建议量填好" in snap["toast"],
                 {"filled": filled, "calls": after_calls, "toast": snap["toast"]})
    shopops_capture(page, "mock-shopops-stockout")

    # 复原满库存 + 样例账本，后续进货/定价用例继续按原基线。
    page.request.get(base + "/__check/shop_report?account=u_shopops&mode=sample&cash=100&stock=full")
    page.evaluate("() => { window.__XKX.refreshShopReport(); return window.__XKX.refreshState('u_shopops'); }")
    page.wait_for_function(
        "() => document.querySelectorAll('#ledgerBody tr').length === 7", timeout=8000)

    # 进货：数量 6，单价 3，合计 18；库存 10 → 16。
    page.fill('input[data-restock-qty="flatbread"]', "6")
    snap = shopops_snapshot(page)
    flat_row = next(r for r in snap["restockRows"] if r["item"] == "flatbread")
    results.must("店铺经营 进货行显示该行合计 18 文",
                 "合计 18 文" in flat_row["text"], flat_row["text"])
    page.click('[data-restock-go="flatbread"]')
    page.wait_for_function(
        "() => document.getElementById('log').textContent.indexOf('库存 10 → 16') >= 0", timeout=8000)
    snap = shopops_snapshot(page)
    results.must("店铺经营 进货成功文案含件数/花费/库存前后",
                 any("进货 炊饼×6" in row and "花费 18 文" in row and "库存 10 → 16" in row
                     for row in snap["log"]), snap["log"])
    shopops_capture(page, "mock-shopops-restock")

    page.request.get(base + "/__check/money?account=u_shopops&value=0")
    page.evaluate("() => window.__XKX.refreshState('u_shopops')")
    page.wait_for_function("() => window.__XKX.commerceState().money === 0", timeout=8000)
    page.click('[data-restock-go="noodle"]')
    page.wait_for_function(
        "() => document.getElementById('errorList').textContent.indexOf('钱不够') >= 0", timeout=8000)
    snap = shopops_snapshot(page)
    results.must("店铺经营 not_enough_money 映射中文且不露原始码",
                 any("钱不够" in e for e in snap["errors"])
                 and all("not_enough_money" not in e for e in snap["errors"]), snap["errors"])
    page.request.get(base + "/__check/money?account=u_shopops&value=2000")
    page.evaluate("() => window.__XKX.refreshState('u_shopops')")
    page.wait_for_function("() => window.__XKX.commerceState().money === 2000", timeout=8000)

    page.fill('input[data-restock-qty="noodle"]', "0")
    page.click('[data-restock-go="noodle"]')
    page.wait_for_function(
        "() => document.getElementById('errorList').textContent.indexOf('数量必须是') >= 0", timeout=8000)
    snap = shopops_snapshot(page)
    results.must("店铺经营 bad_arg:qty 映射中文",
                 any("数量必须是" in e for e in snap["errors"]), snap["errors"])

    # 防重复：慢请求窗口内连点两次，只应产生一次 restock。
    page.request.get(base + "/__check/reset_stats")
    page.request.get(base + "/__check/slow?verb=restock&ms=600")
    page.evaluate("""() => {
      const btn = document.querySelector('[data-restock-go="rice"]');
      if (btn) { btn.click(); btn.click(); }
    }""")
    page.wait_for_timeout(150)
    busy = page.evaluate("""() => {
      const b = document.querySelector('[data-restock-go="rice"]');
      return { disabled: b ? b.disabled : null, text: b ? b.textContent.trim() : '' };
    }""")
    page.wait_for_function(
        "() => document.getElementById('log').textContent.indexOf('进货 米') >= 0", timeout=8000)
    page.wait_for_timeout(200)
    calls = page.request.get(base + "/__check/verb_stats?verb=restock").json()["calls"]
    results.must("店铺经营 进行中按钮禁用并防重复请求",
                 busy["disabled"] is True and "处理中" in busy["text"] and calls == 1,
                 {"busy": busy, "calls": calls})
    page.request.get(base + "/__check/slow?verb=&ms=0")

    # 定价：基准 3，改到 4（+33%），界面同步给后果提示。
    page.fill('input[data-price-input="flatbread"]', "4")
    page.wait_for_function(
        "() => document.querySelector('[data-price-note=\"flatbread\"]').textContent.indexOf('当前基准价 3 文，你的价 4 文，比基准高 33%') >= 0",
        timeout=4000)
    snap = shopops_snapshot(page)
    flat = next(r for r in snap["priceRows"] if r["item"] == "flatbread")
    results.must("店铺经营 定价后果提示（基准/我的价/高低百分比）",
                 "当前基准价 3 文，你的价 4 文，比基准高 33%" in flat["note"]
                 and "过高可能没人买" in flat["note"], flat["note"])
    page.click('[data-price-go="flatbread"]')
    page.wait_for_function(
        "() => document.getElementById('toast').textContent.indexOf('+33%') >= 0", timeout=8000)
    snap = shopops_snapshot(page)
    results.must("店铺经营 改价成功显示 3 → 4 文（+33%）",
                 any("炊饼 3 → 4 文（+33%）" in t for t in [snap["toast"]] + snap["log"]), snap["log"])
    shopops_capture(page, "mock-shopops-price")

    page.fill('input[data-price-input="flatbread"]', "10")
    page.click('[data-price-go="flatbread"]')
    page.wait_for_function(
        "() => document.getElementById('errorList').textContent.indexOf('±20%') >= 0", timeout=8000)
    snap = shopops_snapshot(page)
    results.must("店铺经营 price_out_of_range 映射中文",
                 any("±20%" in e for e in snap["errors"]), snap["errors"])

    # 辞退：先雇王铁匠，再二次确认，欠薪仍计。
    page.click('[data-tab="hire"]')
    page.select_option('select[data-pos-for="npc:wang_tiejiang"]', "铁匠")
    page.fill("#hireWage", "32")
    page.click('[data-hire="npc:wang_tiejiang"]')
    page.wait_for_function(
        "() => document.getElementById('employeeList').textContent.indexOf('王铁匠') >= 0", timeout=8000)
    page.click('[data-tab="business"]')
    page.wait_for_timeout(400)
    page.click('[data-dismiss="npc:wang_tiejiang"]')
    page.wait_for_function("() => !document.getElementById('dismissModal').classList.contains('hidden')",
                           timeout=4000)
    snap = shopops_snapshot(page)
    results.must("店铺经营 辞退二次确认写明欠薪仍计",
                 snap["modalVisible"] and "王铁匠" in snap["modalText"]
                 and "欠薪仍计" in snap["modalText"], snap["modalText"])
    shopops_capture(page, "mock-shopops-dismiss")
    page.click("#dismissConfirm")
    page.wait_for_function(
        "() => document.getElementById('log').textContent.indexOf('欠薪 32 文仍计') >= 0", timeout=8000)
    snap = shopops_snapshot(page)
    results.must("店铺经营 辞退成功返回欠薪数字并从名单移除",
                 any("已辞退 王铁匠" in t and "欠薪 32 文仍计" in t for t in [snap["toast"]] + snap["log"])
                 and all(e["id"] != "npc:wang_tiejiang" for e in snap["employees"]), snap)
    page.evaluate("() => window.__XKX.showError('target_not_employed')")
    snap = shopops_snapshot(page)
    results.must("店铺经营 target_not_employed 映射中文",
                 any("不在你的店里" in e for e in snap["errors"]), snap["errors"])

    # 提现：店现 100 → 80。
    page.fill("#withdrawAmount", "20")
    page.click("#withdrawGo")
    page.wait_for_function(
        "() => document.getElementById('toast').textContent.indexOf('店现 100 → 80') >= 0", timeout=8000)
    snap = shopops_snapshot(page)
    results.must("店铺经营 提现成功显示店现前后",
                 any("提现 20 文，店现 100 → 80" in t for t in [snap["toast"]] + snap["log"])
                 and "当前店现 80 文" in snap["withdrawText"], snap)
    page.fill("#withdrawAmount", "0")
    page.click("#withdrawGo")
    page.wait_for_function(
        "() => document.getElementById('errorList').textContent.indexOf('正整数') >= 0", timeout=8000)
    page.request.get(base + "/__check/shop_report?account=u_shopops&mode=sample&cash=50")
    page.evaluate("() => { window.__XKX.refreshShopReport(); return window.__XKX.refreshState('u_shopops'); }")
    page.wait_for_timeout(500)
    page.fill("#withdrawAmount", "500")
    page.click("#withdrawGo")
    page.wait_for_function(
        "() => document.getElementById('errorList').textContent.indexOf('店铺现金不足') >= 0", timeout=8000)
    snap = shopops_snapshot(page)
    results.must("店铺经营 提现本地校验与店铺现金不足中文提示",
                 any("正整数" in e for e in snap["errors"])
                 and any("店铺现金不足" in e for e in snap["errors"]), snap["errors"])

    # 空账本：开店不足一日。
    page.request.get(base + "/__check/shop_report?account=u_shopops&mode=empty&cash=50")
    page.evaluate("() => { window.__XKX.refreshShopReport(); return window.__XKX.refreshState('u_shopops'); }")
    page.wait_for_function(
        "() => !document.getElementById('ledgerEmpty').classList.contains('hidden')", timeout=8000)
    snap = shopops_snapshot(page)
    results.must("店铺经营 空账本显示「尚无日结记录，世界日结束后来看」",
                 snap["ledger"]["emptyVisible"] and not snap["ledger"]["wrapVisible"]
                 and "尚无日结记录，世界日结束后来看" in snap["ledger"]["emptyText"]
                 and not snap["ledger"]["stockoutVisible"], snap["ledger"])
    shopops_capture(page, "mock-shopops-empty")

    # 移动端 375×812：表格可横滚、页面不溢出、控件 ≥44px。
    page.set_viewport_size({"width": 375, "height": 812})
    page.request.get(base + "/__check/shop_report?account=u_shopops&mode=sample&cash=80")
    page.evaluate("() => { window.__XKX.refreshShopReport(); return window.__XKX.refreshState('u_shopops'); }")
    page.wait_for_function(
        "() => document.querySelectorAll('#ledgerBody tr').length === 7", timeout=8000)
    page.wait_for_timeout(300)
    mobile = page.evaluate("""() => {
      const controls = Array.from(document.querySelectorAll(
        '#restockList input, #restockList button, #priceList input, #priceList button, ' +
        '#withdrawAmount, #withdrawGo, #employeeList [data-dismiss]'))
        .filter(x => !x.classList.contains('hidden'));
      const small = controls.filter(x => x.getBoundingClientRect().height < 44)
        .map(x => (x.id || x.getAttribute('data-restock-go') || x.getAttribute('data-price-input') ||
                   x.getAttribute('data-price-range') || x.textContent.trim()));
      const wrap = document.getElementById('ledgerWrap');
      return {
        pageOverflow: document.documentElement.scrollWidth > window.innerWidth,
        innerWidth: window.innerWidth, scrollWidth: document.documentElement.scrollWidth,
        ledgerScrollable: wrap.scrollWidth > wrap.clientWidth,
        controlCount: controls.length, small: small
      };
    }""")
    results.must("店铺经营 375×812 账本横向滚动且页面不溢出",
                 not mobile["pageOverflow"] and mobile["ledgerScrollable"]
                 and mobile["scrollWidth"] <= mobile["innerWidth"] + 1, mobile)
    results.must("店铺经营 移动端新增控件均 ≥44px",
                 mobile["controlCount"] > 0 and not mobile["small"], mobile)
    shopops_capture(page, "mock-shopops-mobile")

    ctx.close()
    SHOPOPS_EVIDENCE.mkdir(parents=True, exist_ok=True)
    rows = [r for r in results.rows if r["name"].startswith("店铺经营 ")]
    SHOPOPS_EVIDENCE.joinpath("mock-shopops.json").write_text(
        json.dumps({"checks": rows, "failed": sum(1 for r in rows if not r["ok"])},
                   ensure_ascii=False, indent=2), encoding="utf-8")


SECT_EVIDENCE = ROOT / "reports" / "w5-sect"


def sect_snapshot(page):
    return page.evaluate("""() => {
      const s = window.__XKX.sectState();
      const rows = Array.from(document.querySelectorAll('#sectBody .sect-row')).map(r => ({
        id: r.getAttribute('data-sect-disciple') || r.getAttribute('data-recruit-row') || '',
        cls: r.className,
        recruit: !!r.getAttribute('data-recruit-row'),
        text: r.textContent.trim(),
        button: r.querySelector('button') ? r.querySelector('button').textContent.trim() : '',
        disabled: r.querySelector('button') ? r.querySelector('button').disabled : null
      }));
      const pay = document.getElementById('sectPayGo');
      const found = document.getElementById('sectFoundGo');
      const join = document.getElementById('sectJoinGo');
      const dep = document.getElementById('sectDepositGo');
      const wd = document.getElementById('sectWithdrawGo');
      const body = document.getElementById('sectBody');
      return {
        ...s,
        rows: rows,
        body: body ? body.textContent.trim() : '',
        result: document.getElementById('sectResult').textContent.trim(),
        hint: document.getElementById('sectHint').textContent.trim(),
        foundDisabled: found ? found.disabled : null,
        foundText: found ? found.textContent.trim() : '',
        joinDisabled: join ? join.disabled : null,
        payDisabled: pay ? pay.disabled : null,
        payText: pay ? pay.textContent.trim() : '',
        depositDisabled: dep ? dep.disabled : null,
        depositText: dep ? dep.textContent.trim() : '',
        withdrawDisabled: wd ? wd.disabled : null,
        withdrawText: wd ? wd.textContent.trim() : '',
        modalVisible: !document.getElementById('sectModal').classList.contains('hidden'),
        modalText: document.getElementById('sectModalText').textContent.trim(),
        modalAll: document.getElementById('sectModal').textContent.trim(),
        modalError: document.getElementById('sectModalError').textContent.trim(),
        toast: document.getElementById('toast').textContent.trim(),
        errors: Array.from(document.querySelectorAll('#errorList .error-item')).map(e => e.textContent.trim()),
        log: Array.from(document.querySelectorAll('#log .log-item')).map(e => e.textContent.trim())
      };
    }""")

def sect_setup(page, base, account, mode, **params):
    query = "/__check/sect?account=" + quote(account) + "&mode=" + quote(mode)
    for key, value in params.items():
        query += "&" + key + "=" + quote(str(value))
    page.request.get(base + query)
    page.evaluate("(acct) => window.__XKX.refreshState(acct)", account)
    page.wait_for_timeout(300)


def sect_open(page):
    page.click('[data-tab="sect"]')
    page.wait_for_function(
        "() => document.getElementById('pane-sect') && !document.getElementById('pane-sect').classList.contains('hidden')",
        timeout=6000)
    page.wait_for_timeout(200)


def sect_capture(page, name):
    SECT_EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SECT_EVIDENCE / (name + ".png")), full_page=True)


SECT_REJECT_CODES = [
    "no_sect", "insufficient_funds", "insufficient_rep", "already_in_sect",
    "not_sect_leader", "favor_too_low", "trust_too_low", "sect_full",
    "disciple_not_found", "withdraw_over_cap", "treasury_reserve",
    "insufficient_treasury", "already_settled_today",
]


def run_sect(browser, base, results, world):
    # 1) 未入派：门槛三项齐全；余额/声望不足时按钮禁用且写清差额。
    ctx, page = commerce_page(browser, base, "u_sect_founder", "掌门候选")
    sect_setup(page, base, "u_sect_founder", "founder", money=1200, reputation=30)
    sect_open(page)
    page.fill("#sectName", "青竹派")
    snap = sect_snapshot(page)
    results.must("门派 未入派显示 800 文/声望 20 的当前值、需要值与差额",
                 "当前 1200 文 / 需要 800 文 / 已达标" in snap["body"]
                 and "当前 30 / 需要 20 / 已达标" in snap["body"]
                 and snap["foundDisabled"] is False, snap["body"])
    results.must("门派 未入派不出现名册/金库假数据",
                 "名册" not in snap["body"] and "金库" not in snap["body"]
                 and snap["treasury"] is None, snap["body"])

    ctx.close()

    pctx, ppage = commerce_page(browser, base, "u_sect_poor", "贫寒弟子")
    sect_setup(ppage, base, "u_sect_poor", "poor", money=300, reputation=5)
    sect_open(ppage)
    snap = sect_snapshot(ppage)
    results.must("门派 立派门槛不足时按钮禁用并写清差额",
                 snap["foundDisabled"] is True
                 and "当前 300 文 / 需要 800 文 / 差 500 文" in snap["body"]
                 and "当前 5 / 需要 20 / 差 15" in snap["body"], snap["body"])
    sect_capture(ppage, "mock-sect-found-gate")
    pctx.close()

    # 2) 真写动作：立派 → 门主视角，成功文案带真实金库/名册数字。
    ctx, page = commerce_page(browser, base, "u_sect_make", "立派者")
    sect_setup(page, base, "u_sect_make", "founder", money=1200, reputation=30)
    sect_open(page)
    page.fill("#sectName", "青竹派")
    page.fill("#sectDoctrine", "守望相济")
    page.click("#sectFoundGo")
    page.wait_for_function(
        "() => document.getElementById('sectResult').textContent.indexOf('立派成功') >= 0", timeout=8000)
    snap = sect_snapshot(page)
    results.must("门派 立派成功带真实金库余额与名册人数",
                 "立派成功：青竹派" in snap["result"] and "金库 600 文" in snap["result"]
                 and "名册 1 人" in snap["result"]
                 and snap["role"] == "leader", snap["result"])
    sect_capture(page, "mock-sect-founder")

    # 3) 弟子视角：门派/门主/名册/自己的欠饷天数；不渲染门主按钮。
    dctx, dpage = commerce_page(browser, base, "u_sect_disciple", "门下弟子")
    sect_setup(dpage, base, "u_sect_disciple", "disciple", arrears=20)
    sect_open(dpage)
    snap = sect_snapshot(dpage)
    self_row = next((r for r in snap["rows"] if r["id"] == "player:u_sect_disciple"), None)
    results.must("门派 弟子视角显示门派、门主与自己的欠饷天数",
                 snap["role"] == "disciple" and "青竹派" in snap["body"]
                 and "刘三娘" in snap["body"] and self_row is not None
                 and "欠饷 20 文（约 2 天" in self_row["text"], snap["body"])
    results.must("门派 弟子视角不出现门主写操作",
                 "只有门主可以收徒、派俸、驱逐与金库操作" in snap["body"]
                 and snap["payDisabled"] is None
                 and not any(r["button"] == "驱逐" for r in snap["rows"]), snap["body"])
    sect_capture(dpage, "mock-sect-disciple")
    dctx.close()

    # 4) 门主视角：名册字段、收徒门槛、派俸、驱逐二次确认、金库上限。
    ctx.close()
    lctx, lpage = commerce_page(browser, base, "u_sect_leader", "门派掌门")
    sect_setup(lpage, base, "u_sect_leader", "leader", arrears=20, settled=0, treasury=600)
    sect_open(lpage)
    snap = sect_snapshot(lpage)
    wang = next((r for r in snap["rows"] if r["id"] == "npc:wang_tiejiang" and not r["recruit"]), None)
    afu = next((r for r in snap["rows"] if r["id"] == "npc:afu" and r["recruit"]), None)
    results.must("门派 门主名册含技能/好感/信任/欠饷与结算状态缺口标注",
                 wang is not None and "技能 0.55" in wang["text"]
                 and "好感 35" in wang["text"] and "信任 31" in wang["text"]
                 and "欠饷 20 文" in wang["text"]
                 and "暂不可用（缺 disciples[].settled_today）" in wang["text"]
                 and "名册 3 / 上限 3 / 已满" in snap["body"], wang)
    results.must("门派 收徒门槛显示当前/需要/差额且未达标禁用",
                 afu is not None and "好感 12/30（差 18）" in afu["text"]
                 and "信任 8/30（差 22）" in afu["text"] and afu["disabled"] is True, afu)
    sect_capture(lpage, "mock-sect-leader")

    # 派俸：当日一次；成功后回显真实会费/俸禄/金库。
    lpage.click("#sectPayGo")
    lpage.wait_for_function(
        "() => document.getElementById('sectResult').textContent.indexOf('日结完成') >= 0", timeout=8000)
    snap = sect_snapshot(lpage)
    results.must("门派 派俸成功回显真实会费/俸禄/金库与名册",
                 "收会费 10 文" in snap["result"] and "发俸禄 20 文" in snap["result"]
                 and "金库 590 文" in snap["result"] and "名册 3 人" in snap["result"], snap["result"])
    results.must("门派 当日已结算后派俸按钮禁用并回显今日已结算",
                 snap["payDisabled"] is True and "今日已结算" in snap["payText"]
                 and "今日已结算" in snap["body"], {"disabled": snap["payDisabled"], "text": snap["payText"]})

    # 驱逐：二次确认写明欠薪债务仍保留。
    lpage.click('[data-sect-expel="npc:wang_tiejiang"]')
    lpage.wait_for_function(
        "() => !document.getElementById('sectModal').classList.contains('hidden')", timeout=4000)
    snap = sect_snapshot(lpage)
    results.must("门派 驱逐二次确认写明欠薪债务仍保留",
                 snap["modalVisible"] and "王铁匠" in snap["modalText"]
                 and "欠薪债务仍保留" in snap["modalAll"], snap["modalAll"])
    lpage.click("#sectModalCancel")
    lpage.click('[data-sect-expel="npc:wang_tiejiang"]')
    lpage.click("#sectModalConfirm")
    lpage.wait_for_function(
        "() => document.getElementById('sectResult').textContent.indexOf('已驱逐') >= 0", timeout=8000)
    snap = sect_snapshot(lpage)
    results.must("门派 驱逐成功回显真实名册人数",
                 "已驱逐 王铁匠" in snap["result"] and "名册 2 人" in snap["result"]
                 and all(r["id"] != "npc:wang_tiejiang" for r in snap["rows"] if not r["recruit"]),
                 snap["result"])

    # 金库：显示保留金与可提上限；超限禁用；存取回显真实余额。
    results.must("门派 金库显示保留金 100 与单次上限/可提上限",
                 lpage.evaluate("() => window.__XKX.sectState().treasury") == 590,
                 snap["body"])
    lpage.fill("#sectWithdrawAmount", "600")
    snap = sect_snapshot(lpage)
    results.must("门派 取出超限按钮禁用并给可提上限",
                 snap["withdrawDisabled"] is True and "最多可提 490 文" in snap["body"], snap["body"])
    lpage.fill("#sectWithdrawAmount", "50")
    lpage.click("#sectWithdrawGo")
    lpage.wait_for_function(
        "() => document.getElementById('sectResult').textContent.indexOf('已取出 50 文') >= 0", timeout=8000)
    snap = sect_snapshot(lpage)
    results.must("门派 取出成功回显金库真实余额",
                 "已取出 50 文" in snap["result"] and "金库 540 文" in snap["result"], snap["result"])
    lpage.fill("#sectDepositAmount", "100")
    lpage.click("#sectDepositGo")
    lpage.wait_for_function(
        "() => document.getElementById('sectResult').textContent.indexOf('已存入 100 文') >= 0", timeout=8000)
    snap = sect_snapshot(lpage)
    results.must("门派 存入成功回显金库真实余额",
                 "已存入 100 文" in snap["result"] and "金库 640 文" in snap["result"], snap["result"])

    # 在途：显示「处理中…」且按钮禁用。
    sect_setup(lpage, base, "u_sect_leader", "leader", arrears=0, settled=0, treasury=600)
    lpage.request.get(base + "/__check/slow?verb=pay_disciples&ms=900")
    lpage.click("#sectPayGo")
    lpage.wait_for_function(
        "() => document.getElementById('sectPayGo').textContent.indexOf('处理中') >= 0", timeout=4000)
    snap = sect_snapshot(lpage)
    results.must("门派 写动作在途按钮禁用并显示处理中",
                 snap["payDisabled"] is True and "处理中" in snap["payText"], snap)
    lpage.wait_for_function(
        "() => document.getElementById('sectResult').textContent.indexOf('日结完成') >= 0", timeout=10000)
    lpage.request.get(base + "/__check/slow?verb=&ms=0")

    # 13 个拒绝码：全部中文，不落「未知错误」。
    reasons = lpage.evaluate("""(codes) => codes.map(c => [c, window.__XKX.reasonText(c, '')])""",
                             SECT_REJECT_CODES)
    results.must("门派 13 个拒绝码全部有中文文案",
                 all(isinstance(t, str) and t.strip() and "未知" not in t for _, t in reasons),
                 reasons)

    # 缺字段 / 未登录：不渲染空壳假数据。
    mctx, mpage = commerce_page(browser, base, "u_sect_missing", "缺字段")
    sect_setup(mpage, base, "u_sect_missing", "missing")
    sect_open(mpage)
    snap = sect_snapshot(mpage)
    results.must("门派 缺顶层 sect 字段时显示暂不可用且不造假数据",
                 snap["missing"] is True and "未返回顶层 sect 字段" in snap["body"]
                 and "名册" not in snap["body"] and "金库" not in snap["body"], snap["body"])
    mctx.close()

    actx = browser.new_context(viewport={"width": 1280, "height": 900})
    apage = actx.new_page()
    apage.goto(base + "/?ui_check=1")
    wait_app(apage)
    anon = apage.evaluate("""() => ({
      body: (document.getElementById('sectBody') || {}).textContent || '',
      hint: (document.getElementById('sectHint') || {}).textContent || '',
      state: window.__XKX.sectState()
    })""")
    results.must("门派 未登录不渲染空壳假数据",
                 anon["body"].strip() == "" and anon["state"]["inSect"] is False
                 and "登录" in anon["hint"] and anon["state"]["treasury"] is None, anon)
    actx.close()

    # 移动端：门派按钮 ≥44px，页面不横向溢出。
    lctx.close()
    mctx, mpage = commerce_page(browser, base, "u_sect_mobile", "门派移动端",
                                viewport={"width": 375, "height": 812})
    sect_setup(mpage, base, "u_sect_mobile", "leader", arrears=0, settled=0, treasury=600)
    mpage.click("#commerceJump")
    sect_open(mpage)
    mobile = mpage.evaluate("""() => {
      const buttons = Array.from(document.querySelectorAll('#pane-sect button'))
        .filter(b => b.getBoundingClientRect().height > 0);
      const small = buttons.filter(b => b.getBoundingClientRect().height < 44)
        .map(b => ({ text: b.textContent.trim(), h: Math.round(b.getBoundingClientRect().height) }));
      return {
        pageOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
        buttons: buttons.length, small: small
      };
    }""")
    results.must("门派 移动端 375px 按钮 ≥44px 且页面不溢出",
                 mobile["buttons"] > 0 and not mobile["small"] and not mobile["pageOverflow"], mobile)
    sect_capture(mpage, "mock-sect-mobile")
    mctx.close()

    SECT_EVIDENCE.mkdir(parents=True, exist_ok=True)
    rows = [r for r in results.rows if r["name"].startswith("门派 ")]
    SECT_EVIDENCE.joinpath("mock-sect.json").write_text(
        json.dumps({"checks": rows, "failed": sum(1 for r in rows if not r["ok"])},
                   ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="只启动 mock 服务")
    ap.add_argument("--only", default="",
                    help="desktop|mobile|keyboard|auth|reasons|regression|feed|commerce|marriage|shopops|sect，逗号分隔")
    ap.add_argument("--auth", action="store_true", help="--serve 时启用鉴权模式")
    args = ap.parse_args()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    if args.serve:
        world = MockWorld(auth_enabled=args.auth)
        httpd, base = start_server(world)
        print("mock UI:", base + "/?ui_check=1", "(auth=%s)" % args.auth)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            httpd.shutdown()
        return

    results = Results()
    world = MockWorld()
    httpd, base = start_server(world)
    only = set(x.strip() for x in args.only.split(",") if x.strip())
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                if not only or "reasons" in only:
                    run_reasons(browser, base, results)
                if not only or "feed" in only:
                    run_feed(browser, base, results, world)
                if not only or "marriage" in only:
                    run_marriage(browser, base, results, world)
                if not only or "commerce" in only:
                    run_commerce(browser, base, results, world)
                if not only or "shopops" in only:
                    run_shopops(browser, base, results, world)
                if not only or "sect" in only:
                    run_sect(browser, base, results, world)
                if not only or "desktop" in only:
                    run_desktop(browser, base, results)
                if not only or "mobile" in only:
                    run_mobile(browser, base, results)
                if not only or "keyboard" in only:
                    run_keyboard(browser, base, results)
                if not only or "regression" in only:
                    run_regression(browser, base, results)
                if not only or "auth" in only:
                    auth_world = MockWorld(auth_enabled=True)
                    auth_httpd, auth_base = start_server(auth_world)
                    try:
                        run_auth(browser, auth_base, results)
                    finally:
                        auth_httpd.shutdown()
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    results.dump(EVIDENCE_DIR / "checks.json")

    marriage_rows = [r for r in results.rows if r["name"].startswith("婚姻 ")]
    if marriage_rows:
        MARRIAGE_EVIDENCE.mkdir(parents=True, exist_ok=True)
        MARRIAGE_EVIDENCE.joinpath("mock-marriage.json").write_text(
            json.dumps({"checks": marriage_rows,
                        "failed": sum(1 for r in marriage_rows if not r["ok"])},
                       ensure_ascii=False, indent=2), encoding="utf-8")

    # 世界动态专项证据（reports/w4-feed）：桌面 + 移动端检查与匿名性
    feed_rows = [r for r in results.rows
                 if r["name"].startswith("feed ") or r["name"].startswith("移动 世界动态")]
    if feed_rows:
        FEED_EVIDENCE.mkdir(parents=True, exist_ok=True)
        payload = {
            "checks": feed_rows,
            "failed": sum(1 for r in feed_rows if not r["ok"]),
            "feed_calls": world.feed_calls,
            "authorization_headers": world.feed_headers,
            "screenshots": ["reports/w4-feed/feed-desktop.png",
                            "reports/w4-feed/feed-mobile.png"],
        }
        FEED_EVIDENCE.joinpath("feed-checks.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r for r in results.rows if not r["ok"]]
    print("\n%d checks, %d failed; evidence: %s" % (len(results.rows), len(failed), EVIDENCE_DIR))
    if failed:
        for row in failed:
            print("FAILED:", row["name"], row.get("detail"))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
