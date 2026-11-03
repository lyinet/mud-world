"""Effect 应用层：唯一允许修改 WorldState 的地方。

所有 Effect 先在一个 clone 上顺序应用；任一条非法则整个事务丢弃（原子性）。
关系有向：relation_upsert 只写 a -> b；需要双向时由动作显式写两次。
"""
from __future__ import annotations

from typing import Any

from ..core.canonical import normalize
from ..core.models import Effect, EffectError, ScheduleItem
from .state import Business, Debt, Entity, EscortOrder, Faction, Goal, Quest, Relation, WorldState

_ENTITY_FIELDS = {
    "location",
    "home",
    "status",
    "spouse",
    "master",
    "employed_at",
    "business",
    "faction",
    "goal_id",
    "escort_id",
    "role",
    "last_action_tick",
    "presence",
    "last_seen_tick",
}

# 插件只能声明/返回这些原语。`mint` 虽然存在，但只允许 genesis/system 使用，
# 插件 manifest 声明 mint 会在加载期被拒绝（见 plugins/loader.py）。
EFFECT_OPS = frozenset(
    {
        "create_entity",
        "create_business",
        "create_faction",
        "create_account",
        "mint",
        "burn",
        "transfer",
        "item_create",
        "item_consume",
        "item_move",
        "set_location",
        "stamina",
        "skill",
        "set_field",
        "set_employment",
        "relation_upsert",
        "marry",
        "faction_join",
        "add_disciple",
        "debt_create",
        "debt_increase",
        "debt_settle",
        "escort_order_create",
        "escort_start",
        "escort_settle",
        "quest_create",
        "quest_accept",
        "quest_complete",
        "quest_fail",
        "goal_create",
        "goal_progress",
        "goal_abandon",
        "schedule_add",
        "schedule_remove",
        "memory_add",
        "talk_ledger",
        "quest_prune",
        "audit_log",
    }
)

# 插件禁止使用的原语：增发货币必须由内核 genesis/system 控制。
PLUGIN_FORBIDDEN_OPS = frozenset({"mint"})



def _as_int(v: Any, field: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise EffectError(f"{field} 必须是整数")
    return v


def _as_num(v: Any, field: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise EffectError(f"{field} 必须是数字")
    return float(v)


def _entity(state: WorldState, eid: str) -> Entity:
    ent = state.entities.get(eid)
    if ent is None:
        raise EffectError(f"实体不存在: {eid}")
    return ent


def _account(state: WorldState, account: str) -> int:
    if account not in state.accounts:
        raise EffectError(f"账户不存在: {account}")
    return int(state.accounts[account])


def _owner_stock(state: WorldState, owner: str) -> dict[str, int]:
    if owner in state.entities:
        return state.entities[owner].inventory
    if owner in state.businesses:
        return state.businesses[owner].stock
    raise EffectError(f"物品持有者不存在: {owner}")


def party_account(state: WorldState, party: str) -> str:
    if party in state.entities:
        return f"wallet:{party}"
    biz = state.businesses.get(party)
    if biz is not None:
        return biz.cash_account
    fac = state.factions.get(party)
    if fac is not None:
        return fac.treasury_account
    if party in state.accounts:
        return party
    raise EffectError(f"债务方无法映射账户: {party}")


def _apply_one(state: WorldState, e: Effect) -> None:
    op = e.op
    # 事件日志里的 effect data 会经 normalize 落盘（float round(6)）。
    # 实跑时也必须在入口做同样规范化，否则 live 状态保留 1e-17 级精度，
    # 回放用落盘后的 6 位精度重建，跨越舍入边界时哈希会对不上。
    d = normalize(e.data)

    if op == "create_entity":
        ent = Entity(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            role=str(d.get("role", "wanderer")),
            location=str(d["location"]),
            home=str(d.get("home", d["location"])),
            stamina=float(d.get("stamina", 100.0)),
            max_stamina=float(d.get("max_stamina", 100.0)),
            skills={str(k): float(v) for k, v in (d.get("skills") or {}).items()},
            personality=dict(d.get("personality") or {}),
        )
        if ent.id in state.entities:
            raise EffectError(f"实体已存在: {ent.id}")
        if ent.location not in state.locations:
            raise EffectError(f"地点不存在: {ent.location}")
        state.entities[ent.id] = ent
        return

    if op == "create_business":
        bid = str(d["id"])
        if bid in state.businesses:
            raise EffectError(f"商业体已存在: {bid}")
        if d["location"] not in state.locations:
            raise EffectError(f"地点不存在: {d['location']}")
        state.businesses[bid] = Business(
            id=bid,
            name=str(d.get("name", bid)),
            location=str(d["location"]),
            kind=str(d.get("kind", "shop")),
            owner=d.get("owner"),
            cash_account=str(d["cash_account"]),
            employees=[],
        )
        return

    if op == "create_faction":
        fid = str(d["id"])
        if fid in state.factions:
            raise EffectError(f"门派/组织已存在: {fid}")
        state.factions[fid] = Faction(
            id=fid,
            name=str(d.get("name", fid)),
            location=str(d.get("location", "")),
            founder=str(d.get("founder", "")),
            treasury_account=str(d["treasury_account"]),
            leader=d.get("founder"),
        )
        return

    if op == "create_account":
        acc = str(d["account"])
        if acc in state.accounts:
            raise EffectError(f"账户已存在: {acc}")
        state.accounts[acc] = _as_int(d.get("balance", 0), "balance")
        return

    if op == "mint":
        acc = str(d["account"])
        amount = _as_int(d["amount"], "amount")
        if amount <= 0:
            raise EffectError("mint 金额必须为正")
        _account(state, acc)
        state.accounts[acc] += amount
        state.minted_total += amount
        return

    if op == "burn":
        acc = str(d["account"])
        amount = _as_int(d["amount"], "amount")
        if amount <= 0:
            raise EffectError("burn 金额必须为正")
        bal = _account(state, acc)
        if bal < amount:
            raise EffectError(f"burn 余额不足: {acc} {bal} < {amount}")
        state.accounts[acc] -= amount
        state.burned_total += amount
        return

    if op == "transfer":
        src = str(d["src"])
        dst = str(d["dst"])
        amount = _as_int(d["amount"], "amount")
        if amount <= 0:
            raise EffectError("transfer 金额必须为正")
        if src == dst:
            raise EffectError("transfer 源与目标相同")
        bal = _account(state, src)
        _account(state, dst)
        if bal < amount:
            raise EffectError(f"余额不足: {src} {bal} < {amount}")
        state.accounts[src] -= amount
        state.accounts[dst] += amount
        return

    if op in ("item_create", "item_consume", "item_move"):
        item = str(d["item"])
        qty = _as_int(d["qty"], "qty")
        if qty <= 0:
            raise EffectError("物品数量必须为正")
        if op == "item_create":
            stock = _owner_stock(state, str(d["owner"]))
            stock[item] = stock.get(item, 0) + qty
            state.items_created[item] = state.items_created.get(item, 0) + qty
            return
        if op == "item_consume":
            stock = _owner_stock(state, str(d["owner"]))
            if stock.get(item, 0) < qty:
                raise EffectError(f"物品不足: {d['owner']} {item}")
            stock[item] -= qty
            if stock[item] <= 0:
                stock.pop(item, None)
            state.items_burned[item] = state.items_burned.get(item, 0) + qty
            return
        if op == "item_move":
            src_stock = _owner_stock(state, str(d["src"]))
            dst_stock = _owner_stock(state, str(d["dst"]))
            if src_stock.get(item, 0) < qty:
                raise EffectError(f"物品不足: {d['src']} {item}")
            src_stock[item] -= qty
            if src_stock[item] <= 0:
                src_stock.pop(item, None)
            dst_stock[item] = dst_stock.get(item, 0) + qty
            return

    if op == "set_location":
        ent = _entity(state, str(d["entity"]))
        to = str(d["to"])
        if to not in state.locations:
            raise EffectError(f"地点不存在: {to}")
        ent.location = to
        return

    if op == "stamina":
        ent = _entity(state, str(d["entity"]))
        delta = _as_num(d["delta"], "delta")
        ent.stamina = max(0.0, min(ent.max_stamina, ent.stamina + delta))
        return

    if op == "skill":
        ent = _entity(state, str(d["entity"]))
        skill = str(d["skill"])
        delta = _as_num(d["delta"], "delta")
        ent.skills[skill] = max(0.0, min(1.0, float(ent.skills.get(skill, 0.0)) + delta))
        return

    if op == "set_field":
        ent = _entity(state, str(d["entity"]))
        fld = str(d["field"])
        if fld not in _ENTITY_FIELDS:
            raise EffectError(f"字段不可写: {fld}")
        if fld in ("location", "home") and d["value"] not in state.locations:
            raise EffectError(f"地点不存在: {d['value']}")
        setattr(ent, fld, d["value"])
        return

    if op == "set_employment":
        ent = _entity(state, str(d["entity"]))
        bid = str(d["business"])
        biz = state.businesses.get(bid)
        if biz is None:
            raise EffectError(f"商业体不存在: {bid}")
        ent.employed_at = bid
        if ent.id not in biz.employees:
            biz.employees.append(ent.id)
        return

    if op == "relation_upsert":
        a, b = str(d["a"]), str(d["b"])
        if a == b:
            raise EffectError("关系两端相同")
        for eid in (a, b):
            if eid not in state.entities:
                raise EffectError(f"实体不存在: {eid}")
        kind = str(d.get("kind", "friend"))
        key = f"{a}|{b}|{kind}"
        rel = state.relations.get(key)
        if rel is None:
            rel = Relation(
                a=a,
                b=b,
                kind=kind,
                affection=max(-100.0, min(100.0, _as_num(d.get("affection", 0.0), "affection"))),
                trust=max(-100.0, min(100.0, _as_num(d.get("trust", 40.0), "trust"))),
                debt=_as_int(d.get("debt", 0), "debt"),
                status=str(d.get("status", "active")),
            )
            state.relations[key] = rel
        else:
            if "affection_delta" in d:
                rel.affection = max(-100.0, min(100.0, rel.affection + _as_num(d["affection_delta"], "affection_delta")))
            if "trust_delta" in d:
                rel.trust = max(-100.0, min(100.0, rel.trust + _as_num(d["trust_delta"], "trust_delta")))
            if "debt_delta" in d:
                rel.debt += _as_int(d["debt_delta"], "debt_delta")
            if "status" in d:
                rel.status = str(d["status"])
        return

    if op == "marry":
        a, b = str(d["a"]), str(d["b"])
        ea, eb = _entity(state, a), _entity(state, b)
        if ea.spouse is not None or eb.spouse is not None:
            raise EffectError("重婚被拒")
        for e in (ea, eb):
            if any(
                r.kind == "marriage" and r.status == "active" and e.id in (r.a, r.b)
                for r in state.relations.values()
            ):
                raise EffectError(f"{e.id} 已有 active 婚姻关系")
        for x, y in ((a, b), (b, a)):
            state.relations[f"{x}|{y}|marriage"] = Relation(
                a=x, b=y, kind="marriage", affection=80.0, trust=70.0, status="active"
            )
        ea.spouse = b
        eb.spouse = a
        return

    if op == "faction_join":
        ent = _entity(state, str(d["entity"]))
        fid = str(d["faction"])
        fac = state.factions.get(fid)
        if fac is None:
            raise EffectError(f"门派/组织不存在: {fid}")
        if ent.faction is not None and ent.faction != fid:
            # 地点行会（org:）不是终身绑定：可以退出行会、自立门派。
            # 已经在别的门派（sect:）时才拒绝，避免一门多拜。
            if str(ent.faction).startswith("sect:"):
                raise EffectError(f"{ent.id} 已属于其他门派")
            old = state.factions.get(ent.faction)
            if old is not None and ent.id in old.members:
                old.members.remove(ent.id)
        if ent.id not in fac.members:
            fac.members.append(ent.id)
        ent.faction = fid
        return

    if op == "add_disciple":
        master = _entity(state, str(d["master"]))
        disciple = _entity(state, str(d["disciple"]))
        if disciple.id not in master.disciples:
            master.disciples.append(disciple.id)
        return

    if op == "debt_create":
        did = str(d["id"])
        if did in state.debts:
            raise EffectError(f"债务已存在: {did}")
        creditor, debtor = str(d["creditor"]), str(d["debtor"])
        if creditor == debtor:
            raise EffectError("债务两端相同")
        amount = _as_int(d["amount"], "amount")
        if amount <= 0:
            raise EffectError("债务金额必须为正")
        party_account(state, creditor)
        party_account(state, debtor)
        state.debts[did] = Debt(
            id=did,
            creditor=creditor,
            debtor=debtor,
            amount=amount,
            long_term=bool(d.get("long_term", False)),
            status="active",
            created_tick=_as_int(d.get("created_tick", 0), "created_tick"),
            note=str(d.get("note", "")),
        )
        return

    if op == "debt_increase":
        did = str(d["id"])
        debt = state.debts.get(did)
        if debt is None:
            raise EffectError(f"债务不存在: {did}")
        if debt.status != "active":
            raise EffectError("债务已结清")
        amount = _as_int(d["amount"], "amount")
        if amount <= 0:
            raise EffectError("债务金额必须为正")
        debt.amount += amount
        return

    if op == "debt_settle":
        did = str(d["id"])
        debt = state.debts.get(did)
        if debt is None:
            raise EffectError(f"债务不存在: {did}")
        if debt.status != "active":
            raise EffectError("债务已结清")
        amount = _as_int(d["amount"], "amount")
        if amount <= 0 or amount > debt.amount:
            raise EffectError("还款金额非法")
        src = party_account(state, debt.debtor)
        dst = party_account(state, debt.creditor)
        if state.account_balance(src) < amount:
            raise EffectError("债务人余额不足")
        state.accounts[src] -= amount
        state.accounts[dst] += amount
        debt.amount -= amount
        if debt.amount == 0:
            debt.status = "settled"
        return

    if op == "escort_order_create":
        oid = str(d["id"])
        if oid in state.escorts:
            raise EffectError(f"镖单已存在: {oid}")
        origin, dest = str(d["origin"]), str(d["destination"])
        for lid in (origin, dest):
            if lid not in state.locations:
                raise EffectError(f"地点不存在: {lid}")
        cargo = _as_int(d["cargo_value"], "cargo_value")
        fee = _as_int(d["fee"], "fee")
        deposit = _as_int(d["deposit"], "deposit")
        if cargo <= 0 or fee <= 0 or deposit <= 0:
            raise EffectError("镖单金额非法")
        state.escorts[oid] = EscortOrder(
            id=oid,
            origin=origin,
            destination=dest,
            cargo_value=cargo,
            fee=fee,
            deposit=deposit,
            status="open",
            created_tick=_as_int(d.get("created_tick", 0), "created_tick"),
        )
        return

    if op == "escort_start":
        oid = str(d["id"])
        order = state.escorts.get(oid)
        if order is None:
            raise EffectError(f"镖单不存在: {oid}")
        if order.status != "open":
            raise EffectError("镖单已被接走")
        carrier = _entity(state, str(d["carrier"]))
        if carrier.escort_id is not None:
            raise EffectError(f"{carrier.id} 已有在途镖单")
        order.status = "active"
        order.carrier = carrier.id
        order.success = bool(d["success"])
        order.arrive_tick = _as_int(d["arrive_tick"], "arrive_tick")
        order.route_id = str(d.get("route_id", ""))
        order.agency_cut = _as_num(d.get("agency_cut", 0.0), "agency_cut")
        order.fee_penalty = _as_int(d.get("fee_penalty", 0), "fee_penalty")
        order.cargo_loss_ratio = _as_num(d.get("cargo_loss_ratio", 0.0), "cargo_loss_ratio")
        order.risk_event = str(d.get("risk_event", ""))
        order.risk_stamina = _as_num(d.get("risk_stamina", 0.0), "risk_stamina")
        carrier.escort_id = oid
        return

    if op == "quest_create":
        qid = str(d["id"])
        if qid in state.quests:
            raise EffectError(f"任务已存在: {qid}")
        giver = str(d["giver"])
        tid = str(d["template_id"])
        _entity(state, giver)
        if d["target_location"] not in state.locations:
            raise EffectError(f"地点不存在: {d['target_location']}")
        state.quests[qid] = Quest(
            id=qid,
            template_id=str(d["template_id"]),
            giver=giver,
            acceptor=None,
            target_npc=str(d.get("target_npc", "")),
            target_location=str(d["target_location"]),
            verb=str(d["verb"]),
            args=dict(d.get("args") or {}),
            reward_money=_as_int(d.get("reward_money", 0), "reward_money"),
            reward_relation=_as_num(d.get("reward_relation", 0.0), "reward_relation"),
            deadline_tick=_as_int(d["deadline_tick"], "deadline_tick"),
            created_tick=_as_int(d.get("created_tick", 0), "created_tick"),
            settlement_amount=_as_int(d.get("settlement_amount", 0), "settlement_amount"),
        )
        state.quest_stats.setdefault(
            tid, {"created": 0, "completed": 0, "failed": 0, "expired": 0}
        )["created"] += 1
        return

    if op == "quest_accept":
        qid = str(d["id"])
        quest = state.quests.get(qid)
        if quest is None:
            raise EffectError(f"任务不存在: {qid}")
        if quest.status != "open":
            raise EffectError("任务已被接走")
        ent = _entity(state, str(d["actor"]))
        quest.status = "accepted"
        quest.acceptor = ent.id
        quest.accepted_tick = _as_int(d.get("tick", 0), "tick")
        return

    if op in ("quest_complete", "quest_fail"):
        qid = str(d["id"])
        quest = state.quests.get(qid)
        if quest is None:
            raise EffectError(f"任务不存在: {qid}")
        # 无人接单的 open 任务过期后关闭为 expired：否则它会永久占住模板名，
        # 让 system_quest_post 再也不发新单（镖单 32→25 的根因）。
        stats = state.quest_stats.setdefault(
            quest.template_id, {"created": 0, "completed": 0, "failed": 0, "expired": 0}
        )
        if quest.status == "open" and op == "quest_fail":
            quest.status = "expired"
            quest.completed_tick = _as_int(d.get("tick", 0), "tick")
            stats["expired"] += 1
            state.quests_expired_total += 1
            return
        if quest.status != "accepted":
            raise EffectError("任务不在进行中")
        quest.status = "completed" if op == "quest_complete" else "failed"
        quest.completed_tick = _as_int(d.get("tick", 0), "tick")
        if quest.status == "completed":
            stats["completed"] += 1
        else:
            stats["failed"] += 1
        return

    if op == "quest_prune":
        ids = d.get("ids") or []
        if not isinstance(ids, list):
            raise EffectError("quest_prune.ids 必须是列表")
        for qid in ids:
            quest = state.quests.get(str(qid))
            if quest is None:
                continue
            if quest.status in ("open", "accepted"):
                raise EffectError(f"不能清理在途任务: {qid}")
            state.quests.pop(str(qid), None)
        return

    if op == "escort_settle":
        oid = str(d["id"])
        order = state.escorts.get(oid)
        if order is None:
            raise EffectError(f"镖单不存在: {oid}")
        if order.status != "active":
            raise EffectError("镖单不在在途状态")
        order.status = "done" if order.success else "failed"
        if order.carrier and order.carrier in state.entities:
            state.entities[order.carrier].escort_id = None
        return

    if op == "goal_create":
        gid = str(d["id"])
        if gid in state.goals:
            raise EffectError(f"目标已存在: {gid}")
        npc = str(d["npc"])
        _entity(state, npc)
        state.goals[gid] = Goal(
            id=gid,
            npc=npc,
            kind=str(d["kind"]),
            target=max(0.01, _as_num(d.get("target", 1.0), "target")),
            status="active",
            created_tick=_as_int(d.get("created_tick", 0), "created_tick"),
            source=str(d.get("source", "npc")),
            content_goal_id=str(d.get("content_goal_id", "")),
            content_type=str(d.get("content_type", "")),
        )
        _entity(state, npc).goal_id = gid
        return

    if op == "goal_progress":
        gid = str(d["id"])
        goal = state.goals.get(gid)
        if goal is None:
            raise EffectError(f"目标不存在: {gid}")
        delta = _as_num(d["delta"], "delta")
        if delta < 0:
            raise EffectError("目标进度不得回退（K1 单调性）")
        if goal.status != "active":
            return
        tick = _as_int(d.get("tick", 0), "tick")
        goal.progress = min(1.0, max(0.0, goal.progress + delta))
        goal.progress_log.append((tick, goal.progress))
        if goal.progress >= 1.0:
            goal.status = "completed"
            ent = state.entities.get(goal.npc)
            if ent is not None and ent.goal_id == gid:
                ent.goal_id = None
        return

    if op == "goal_abandon":
        gid = str(d["id"])
        goal = state.goals.get(gid)
        if goal is None:
            raise EffectError(f"目标不存在: {gid}")
        goal.status = "abandoned"
        return

    if op == "schedule_add":
        sid = str(d["id"])
        if sid in state.schedule:
            raise EffectError(f"计划已存在: {sid}")
        tick = _as_int(d["tick"], "tick")
        if tick < 0:
            raise EffectError("计划 tick 非法")
        actor = str(d["actor"])
        _entity(state, actor)
        state.schedule[sid] = ScheduleItem(
            id=sid,
            tick=tick,
            actor=actor,
            verb=str(d["verb"]),
            args=dict(d.get("args") or {}),
            created_tick=_as_int(d.get("created_tick", 0), "created_tick"),
        )
        return

    if op == "schedule_remove":
        state.schedule.pop(str(d["id"]), None)
        return

    if op == "memory_add":
        npc = str(d["npc"])
        _entity(state, npc)
        entry = {
            "tick": _as_int(d.get("tick", 0), "tick"),
            "text": str(d.get("text", "")),
            "importance": _as_num(d.get("importance", 0.1), "importance"),
            "source": str(d.get("source", "")),
        }
        mem = state.memories.setdefault(npc, [])
        mem.append(entry)
        if len(mem) > 80:
            del mem[: len(mem) - 80]
        return

    if op == "audit_log":
        kind = str(d.get("kind", "")).strip()
        if not kind:
            raise EffectError("audit kind 不能为空")
        entry = {
            "kind": kind,
            "actor": str(d.get("actor", "")),
            "tick": _as_int(d.get("tick", 0), "tick"),
            "fields": [str(x) for x in (d.get("fields") or [])],
            "note": str(d.get("note", "")),
        }
        state.audit_log.append(entry)
        if len(state.audit_log) > 500:
            del state.audit_log[: len(state.audit_log) - 500]
        return

    if op == "talk_ledger":
        npc = str(d["npc"])
        _entity(state, npc)
        state.talk_ledger[npc] = {
            "day": _as_int(d.get("day", 0), "day"),
            "day_affection": _as_num(d.get("day_affection", 0.0), "day_affection"),
            "day_trust": _as_num(d.get("day_trust", 0.0), "day_trust"),
            "last_tick": _as_int(d.get("last_tick", 0), "last_tick"),
            "tick_affection": _as_num(d.get("tick_affection", 0.0), "tick_affection"),
            "tick_trust": _as_num(d.get("tick_trust", 0.0), "tick_trust"),
        }
        return

    raise EffectError(f"未知 Effect: {op}")


def apply_effects(state: WorldState, effects: list[Effect]) -> WorldState:
    for e in effects:
        _apply_one(state, e)
    return state


def apply_atomic(state: WorldState, effects: list[Effect]) -> WorldState:
    trial = state.clone()
    apply_effects(trial, effects)
    return trial


def effects_to_dicts(effects: list[Effect]) -> list[dict[str, Any]]:
    return [normalize(e.to_dict()) for e in effects]
