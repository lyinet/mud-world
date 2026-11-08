"""Effect 应用层：唯一允许修改 WorldState 的地方。

所有 Effect 先在一个 clone 上顺序应用；任一条非法则整个事务丢弃（原子性）。
关系有向：relation_upsert 只写 a -> b；需要双向时由动作显式写两次。
"""
from __future__ import annotations

from typing import Any

from ..core.canonical import normalize
from ..core.models import Effect, EffectError, ScheduleItem
from .state import (
    Business,
    Debt,
    Entity,
    EscortOrder,
    Faction,
    Goal,
    PlayerProfile,
    Quest,
    Relation,
    WorldState,
)

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
    # NPC 主动攻击交战状态（只由 xkx 规则写入）。
    "combat_target",
    "combat_tick",
    "combat_round",
    # 原版 `env/wimpy` 阈值（玩家命令 wimpy 写入，0..80）。
    "wimpy",
    # NPC 原版 `die()` 固定掉落（内容包 drops，死亡时转地面物品）。
    "drops",
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
        "shop_hire",
        "shop_dismiss",
        "shop_price",
        "shop_history",
        "shop_report",
        "shop_day_close",
        "relation_upsert",
        "marry",
        "marriage_meta",
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
        "quest_step",
        "equip",
        "unequip",
        "goal_create",
        "goal_progress",
        "goal_abandon",
        "schedule_add",
        "schedule_remove",
        "memory_add",
        "talk_ledger",
        "quest_prune",
        "audit_log",
        "player_upsert",
        "player_presence",
        "player_endow",
        # ---- 战斗原语（xkx parity，P2）----
        # 伤害/死亡/复活/掉落全部是纯 Effect：投影只能由事件日志重建，
        # 战斗动作不得直接改 state。
        "hp_add",
        "hp_set",
        "entity_status",
        "revive",
        "loot_create",
        # 从房间地面取物（loot/get 的唯一变更载体）。
        "loot_take",
        # 旧玩家实体补气血/属性（只在 max_hp<=0 时由 player_enter 发一次）。
        "vitals_set",
        "combat_exp_add",
        # 动态任务奖励（xkx parity）：潜力与声望，只在任务结算时增加。
        "potential_add",
        "score_add",
        # xkx 任务进度：写入/推进 "actor|quest" 记录，可回放。
        "xkx_quest_upsert",
        "skill_xp_add",
        # 公式库 improve_skill 的落库载体：直接写回等级/熟练度（纯 Effect）。
        "skill_progress",
        # 成长线（学习/练习）：潜能消耗与已消耗学习点账本。
        "potential_spend",
        "learned_points_add",
        # 原版 die()/killer_reward 的玩家死亡结算与只读死亡明细。
        "player_death",
        "death_log",
        # 成长明细（拜师/学习/练习）：只读，随事件持久化。
        "growth_log",
        # 只读战报载体（与 shop_report 同类）：不修改状态，只把 rounds[] 写进事件。
        "combat_log",
        # NPC 主动攻击判定明细（只读）：whether/roll/round 写进事件，便于审计与验收。
        "aggression_log",
        # 原版 wimpy 自动逃跑明细（只读）：roll/出口/成败，供审计。
        "wimpy_log",
    }
)

PLAYER_PRESENCE_STATES = frozenset({"online", "offline", "custody", "dormant"})

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


def _player_shop_by_account(state: WorldState, account: str):
    """按公账反查玩家店铺；非玩家店铺返回 None（NPC 店铺不参与日结）。"""
    for biz in state.businesses.values():
        if biz.cash_account == account and biz.owner and str(biz.owner).startswith("player:"):
            return biz
    return None


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
        # 参考快照初始状态（P4a）：原版 NPC 的 <昏迷不醒> 是战斗临时态，
        # 内容包用 initial_state 显式标注；旧事件不带该键，仍为 alive。
        if "status" in d:
            status = str(d.get("status") or "alive")
            if status not in ("alive", "unconcious"):
                raise EffectError(f"非法实体状态: {status}")
            ent.status = status
        if ent.id in state.entities:
            raise EffectError(f"实体已存在: {ent.id}")
        if ent.location not in state.locations:
            raise EffectError(f"地点不存在: {ent.location}")
        # 战斗字段只在内容包显式声明时写入：旧内容包 create_entity 不带这些键，
        # 实体保持默认值，to_dict() 也不输出，旧世界哈希不受影响。
        if "max_hp" in d:
            ent.max_hp = _as_int(d.get("max_hp", 0), "max_hp")
            ent.hp = max(0, min(ent.max_hp, _as_int(d.get("hp", ent.max_hp), "hp")))
        if "attack" in d:
            ent.attack = _as_int(d["attack"], "attack")
        if "defense" in d:
            ent.defense = _as_int(d["defense"], "defense")
        if "temp_apply" in d:
            ent.temp_apply = {
                str(k): _as_int(v, f"temp_apply.{k}") for k, v in dict(d["temp_apply"] or {}).items()
            }
        if "equipment" in d:
            ent.equipment = [str(x) for x in (d.get("equipment") or []) if str(x)]
        # 创世即穿戴的装备（NPC 的 `equipment` 落地）：与 `equip` 效果同一套字段，
        # 只写非空值，旧内容包（无装备）的事件与哈希逐字节不变。
        if "equipped" in d:
            ent.equipped = {
                str(k): str(v) for k, v in dict(d.get("equipped") or {}).items() if str(v)
            }
        if "equipped_apply" in d:
            ent.equipped_apply = {
                str(k): _as_int(v, f"equipped_apply.{k}")
                for k, v in dict(d.get("equipped_apply") or {}).items()
                if str(k).startswith("apply/")
            }
        if "weapon_type" in d:
            ent.weapon_type = str(d.get("weapon_type") or "")
        if "attrs" in d:
            ent.attrs = {str(k): _as_int(v, f"attrs.{k}") for k, v in dict(d["attrs"] or {}).items()}
        if "combat_exp" in d:
            ent.combat_exp = _as_int(d["combat_exp"], "combat_exp")
        if "skill_levels" in d:
            ent.skill_levels = {
                str(k): max(0, min(800, _as_int(v, f"skill_levels.{k}")))
                for k, v in dict(d["skill_levels"] or {}).items()
            }
        if "skill_xp" in d:
            ent.skill_xp = {
                str(k): max(0, min(99, _as_int(v, f"skill_xp.{k}")))
                for k, v in dict(d["skill_xp"] or {}).items()
            }
        if "potential" in d:
            ent.potential = max(0, _as_int(d["potential"], "potential"))
        if "learned_points" in d:
            ent.learned_points = max(0, _as_int(d["learned_points"], "learned_points"))
        if "map_skills" in d:
            ent.map_skills = {
                str(k): str(v) for k, v in dict(d["map_skills"] or {}).items() if str(v)
            }
        if "prepare_skills" in d:
            ent.prepare_skills = [str(x) for x in (d.get("prepare_skills") or []) if str(x)]
        if "race" in d:
            ent.race = str(d.get("race") or "")
        for key in ("neili", "max_neili", "jiali"):
            if key in d:
                setattr(ent, key, max(0, _as_int(d[key], key)))
        # 同模板多实例：旧事件不带这两个键，规范实体保持空/0。
        if "template" in d:
            ent.template = str(d.get("template") or "")
        if "instance" in d:
            ent.instance = max(0, _as_int(d.get("instance", 0), "instance"))
        if "wimpy" in d:
            ent.wimpy = max(0, min(80, _as_int(d.get("wimpy", 0), "wimpy")))
        if "drops" in d:
            ent.drops = {
                str(k): max(0, _as_int(v, f"drops.{k}"))
                for k, v in dict(d.get("drops") or {}).items()
                if str(k) and _as_int(v, f"drops.{k}") > 0
            }
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
        # 玩家店铺当日流水：营收=顾客买入入公账，进货=公账付给卖方。
        # 只累计运行期计数，不改变任何持久字段，日结事件统一结算。
        reason = str(d.get("reason", ""))
        # 顾客买入与在店里消费/吃饭都算店铺营收；向卖方付款（sell/restock）算进货。
        # withdraw 是店主提现，既不是营收也不是进货，不进入日结流水。
        if reason in ("buy", "consume"):
            shop = _player_shop_by_account(state, dst)
            if shop is not None:
                shop.day_revenue += amount
                shop.day_customers += 1
        elif reason in ("sell", "restock"):
            shop = _player_shop_by_account(state, src)
            if shop is not None:
                shop.day_restock += amount
        return

    if op == "shop_day_close":
        # 日结完成后清零计数并结转欠税：这些是运行期字段，不进 state 字典。
        arrears = d.get("tax_arrears") or {}
        for biz in state.businesses.values():
            biz.day_revenue = 0
            biz.day_restock = 0
            biz.day_customers = 0
        for bid, amount in dict(arrears).items():
            biz = state.businesses.get(str(bid))
            if biz is not None:
                biz.tax_arrears = max(0, _as_int(amount, "tax_arrears"))
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

    # ---- 战斗原语（P2）：伤害 / 死亡 / 复活 / 掉落 ----
    # 这些分支只读事件里的整数参数，不做随机、不看墙钟；战斗结果因此可逐字节回放。
    if op in ("hp_add", "hp_set"):
        ent = _entity(state, str(d["entity"]))
        if ent.max_hp <= 0:
            raise EffectError(f"实体没有战斗生命值: {ent.id}")
        old = int(ent.hp)
        if op == "hp_add":
            value = old + _as_int(d["delta"], "delta")
        else:
            value = _as_int(d["value"], "value")
        new = max(0, min(ent.max_hp, value))
        if op == "hp_add":
            # 事件是唯一事实源：钳制就地进行，并把**实际**增量写回 delta。
            # 否则“超额伤害”会让事件记 -71、状态只掉 -70，重放永远对不上。
            # normalize() 给了 d 一份副本，事件序列化读的是 e.data，两边都写。
            d["delta"] = new - old
            if isinstance(e.data, dict):
                e.data["delta"] = new - old
        ent.hp = new
        return

    if op == "entity_status":
        ent = _entity(state, str(d["entity"]))
        status = str(d["status"])
        if status not in ("alive", "dead", "reincarnated"):
            raise EffectError(f"非法实体状态: {status}")
        if status == "dead":
            if ent.hp > 0:
                raise EffectError("死亡前 hp 必须为 0（伤害必须由 hp_add 先行结算）")
            ent.status = "dead"
            ent.death_tick = _as_int(d.get("tick", 0), "tick")
            ent.death_location = str(d.get("location", ent.location))
        else:
            ent.status = status
            ent.death_tick = 0
            ent.death_location = ""
        return

    if op == "revive":
        ent = _entity(state, str(d["entity"]))
        if ent.max_hp <= 0:
            raise EffectError(f"实体没有战斗生命值: {ent.id}")
        if ent.status != "dead":
            raise EffectError(f"实体未死亡，不能复活: {ent.id}")
        hp = _as_int(d["hp"], "hp") if "hp" in d else max(1, ent.max_hp // 2)
        ent.hp = max(1, min(ent.max_hp, hp))
        ent.status = "alive"
        ent.death_tick = 0
        ent.death_location = ""
        return

    if op == "entity_respawn":
        # 房间 reset：把死亡槽位恢复到创世基线（原版是 destruct 后重新 new 一个
        # clone；我们用同一实体 id 重新初始化，保持槽位/关系/任务引用稳定）。
        ent = _entity(state, str(d["entity"]))
        if ent.id.startswith("player:"):
            raise EffectError(f"玩家实体不能由 reset 重生: {ent.id}")
        if ent.status != "dead":
            raise EffectError(f"实体未死亡，不能 reset 重生: {ent.id}")
        # 审计留痕：把这次死亡的时间/地点移入 last_death_*，再清 death_*。
        ent.last_death_tick = _as_int(d.get("death_tick", ent.death_tick), "death_tick")
        ent.last_death_location = str(d.get("death_location", ent.death_location))
        ent.death_tick = 0
        ent.death_location = ""
        ent.status = "alive"
        if "location" in d:
            loc = str(d["location"])
            if loc not in state.locations:
                raise EffectError(f"地点不存在: {loc}")
            ent.location = loc
        for key in ("max_hp", "hp", "attack", "defense", "combat_exp", "max_stamina", "stamina",
                    "neili", "max_neili", "jiali"):
            if key in d:
                setattr(ent, key, _as_int(d[key], key) if key != "stamina" and key != "max_stamina"
                        else _as_num(d[key], key))
        for key in ("attrs", "skill_levels", "skill_xp", "temp_apply", "inventory"):
            if key in d:
                setattr(ent, key, {str(k): int(v) for k, v in dict(d[key]).items()})
        if "map_skills" in d:
            ent.map_skills = {str(k): str(v) for k, v in dict(d["map_skills"]).items()}
        if "prepare_skills" in d:
            ent.prepare_skills = [str(x) for x in (d["prepare_skills"] or [])]
        if "skills" in d:
            ent.skills = {str(k): float(v) for k, v in dict(d["skills"]).items()}
        if "equipment" in d:
            ent.equipment = [str(x) for x in (d["equipment"] or [])]
        if "equipped" in d:
            ent.equipped = dict(sorted((str(k), str(v)) for k, v in dict(d["equipped"]).items()))
        if "equipped_apply" in d:
            ent.equipped_apply = dict(sorted((str(k), int(v)) for k, v in dict(d["equipped_apply"]).items()))
        if "weapon_type" in d:
            ent.weapon_type = str(d["weapon_type"])
        if "race" in d:
            ent.race = str(d["race"])
        return

    if op == "loot_create":
        # 掉落到**房间地面**，不直接进背包。`location` 优先；缺省用实体所在地，
        # 兼容旧事件里只写 entity 的写法。
        item = str(d["item"])
        qty = _as_int(d["qty"], "qty")
        if qty <= 0:
            raise EffectError("掉落数量必须为正")
        loc = str(d.get("location") or "")
        if not loc:
            loc = _entity(state, str(d["entity"])).location
        if loc not in state.locations:
            raise EffectError(f"地点不存在: {loc}")
        bucket = state.ground.setdefault(loc, {})
        bucket[item] = int(bucket.get(item, 0)) + qty
        state.items_created[item] = state.items_created.get(item, 0) + qty
        return

    if op == "loot_take":
        # 地面 → 背包。数量不足直接报错，由外层事务整体回滚，不会半取。
        ent = _entity(state, str(d["entity"]))
        loc = str(d["location"])
        item = str(d["item"])
        qty = _as_int(d["qty"], "qty")
        if qty <= 0:
            raise EffectError("取物数量必须为正")
        bucket = state.ground.get(loc) or {}
        if int(bucket.get(item, 0)) < qty:
            raise EffectError(f"地面物品不足: {loc} {item}")
        left = int(bucket[item]) - qty
        if left > 0:
            bucket[item] = left
        else:
            bucket.pop(item, None)
        if not bucket:
            state.ground.pop(loc, None)
        ent.inventory[item] = int(ent.inventory.get(item, 0)) + qty
        return

    if op == "vitals_set":
        # 只给没有战斗字段的旧实体补一次基础气血/属性；内容包创世不经过这里。
        ent = _entity(state, str(d["entity"]))
        max_hp = _as_int(d["max_hp"], "max_hp")
        if max_hp <= 0:
            raise EffectError("vitals_set 的 max_hp 必须为正")
        ent.max_hp = max_hp
        ent.hp = max(0, min(max_hp, _as_int(d.get("hp", max_hp), "hp")))
        if "attrs" in d:
            ent.attrs = {str(k): _as_int(v, f"attrs.{k}") for k, v in dict(d["attrs"] or {}).items()}
        if "combat_exp" in d:
            ent.combat_exp = max(0, _as_int(d["combat_exp"], "combat_exp"))
        if "skill_levels" in d:
            ent.skill_levels = {
                str(k): max(0, min(800, _as_int(v, f"skill_levels.{k}")))
                for k, v in dict(d["skill_levels"] or {}).items()
            }
        if "potential" in d and not ent.potential:
            ent.potential = max(0, _as_int(d["potential"], "potential"))
        if "map_skills" in d:
            ent.map_skills = {
                str(k): str(v) for k, v in dict(d["map_skills"] or {}).items() if str(v)
            }
        if "prepare_skills" in d:
            ent.prepare_skills = [str(x) for x in (d.get("prepare_skills") or []) if str(x)]
        if "race" in d:
            ent.race = str(d.get("race") or "")
        for key in ("neili", "max_neili", "jiali"):
            if key in d:
                setattr(ent, key, max(0, _as_int(d[key], key)))
        return

    if op == "combat_exp_add":
        ent = _entity(state, str(d["entity"]))
        delta = _as_int(d["delta"], "delta")
        if delta < 0:
            raise EffectError("战斗经验不得为负")
        ent.combat_exp += delta
        return

    if op == "potential_add":
        ent = _entity(state, str(d["entity"]))
        delta = _as_int(d["delta"], "delta")
        if delta <= 0:
            raise EffectError("潜力增量必须为正")
        ent.potential += delta
        return

    if op == "score_add":
        ent = _entity(state, str(d["entity"]))
        delta = _as_int(d["delta"], "delta")
        if delta <= 0:
            raise EffectError("声望增量必须为正")
        ent.score += delta
        return

    if op == "xkx_quest_upsert":
        # 唯一的 xkx 任务进度写入口。状态只有 accepted / completed；
        # 完成不可回退，重复接取只是推进 step，不会覆盖已完成记录。
        actor = _entity(state, str(d["actor"]))
        qid = str(d["quest"])
        status = str(d["status"])
        if status not in ("accepted", "completed"):
            raise EffectError(f"未知任务状态: {status}")
        key = f"{actor.id}|{qid}"
        cur = state.xkx_quests.get(key)
        if cur is None:
            # 动态任务没有问话前置，交付即完成；问话支线必须先 accepted。
            if status != "accepted" and not d.get("implicit"):
                raise EffectError("任务尚未接取")
            cur = {
                "actor": actor.id,
                "quest": qid,
                "status": "accepted",
                "step": str(d.get("step", "asked")),
                "accepted_tick": _as_int(d.get("tick", 0), "tick"),
            }
            state.xkx_quests[key] = cur
        if cur["status"] == "completed":
            raise EffectError("任务已完成")
        if "step" in d:
            cur["step"] = str(d["step"])
        if "talked_tick" in d:
            cur["talked_tick"] = _as_int(d["talked_tick"], "talked_tick")
        if status == "completed":
            cur["status"] = "completed"
            cur["completed_tick"] = _as_int(d.get("tick", 0), "tick")
            reward = d.get("reward")
            if reward is not None:
                if not isinstance(reward, dict):
                    raise EffectError("任务奖励必须是对象")
                cur["reward"] = {
                    "exp": _as_int(reward.get("exp", 0), "reward.exp"),
                    "potential": _as_int(reward.get("potential", 0), "reward.potential"),
                    "score": _as_int(reward.get("score", 0), "reward.score"),
                }
        return

    if op == "skill_xp_add":
        ent = _entity(state, str(d["entity"]))
        skill = str(d["skill"])
        delta = _as_int(d["delta"], "delta")
        if delta <= 0:
            raise EffectError("技能经验增量必须为正")
        level = int(ent.skill_levels.get(skill, 0))
        xp = int(ent.skill_xp.get(skill, 0)) + delta
        # 100 经验升 1 级，上限 800；多余经验留在 xp（封顶 99，避免无界增长）。
        while xp >= 100 and level < 800:
            xp -= 100
            level += 1
        ent.skill_levels[skill] = level
        ent.skill_xp[skill] = min(99, max(0, xp))
        return

    if op == "skill_progress":
        ent = _entity(state, str(d["entity"]))
        skill = str(d["skill"])
        level = _as_int(d["level"], "level")
        learned = _as_int(d["learned"], "learned")
        if level < 0 or level > 800:
            raise EffectError("技能等级必须在 0..800")
        if learned < 0:
            raise EffectError("技能熟练度不得为负")
        ent.skill_levels[skill] = level
        ent.skill_xp[skill] = learned
        return

    if op == "potential_spend":
        ent = _entity(state, str(d["entity"]))
        cost = _as_int(d["cost"], "cost")
        if cost <= 0:
            raise EffectError("潜能消耗必须为正")
        ent.potential = max(0, int(ent.potential) - cost)
        return

    if op == "learned_points_add":
        ent = _entity(state, str(d["entity"]))
        ent.learned_points = max(0, int(ent.learned_points) + _as_int(d["delta"], "delta"))
        return

    if op == "player_death":
        # 原版 die()/killer_reward() 的死亡后果，逐条照搬，不额外加码：
        #   1) combat_exp -= combat_exp/50（C 整数除法）
        #   2) potential 高于 learned_points 时，potential += (learned_points-potential)/2
        #   3) skill_death_penalty：每门已学技能等级 -1，熟练度清零；归零则删条目
        #   4) 死亡计数 +1
        ent = _entity(state, str(d["entity"]))
        if ent.status != "dead":
            raise EffectError(f"实体未死亡，不能结算死亡后果: {ent.id}")
        exp_before = int(ent.combat_exp)
        exp_loss = exp_before // 50
        ent.combat_exp = max(0, exp_before - exp_loss)
        if ent.potential > ent.learned_points:
            # C 语言整数除法向零截断，Python 的 / 配合 int() 才能逐值一致。
            ent.potential = max(0, int(ent.potential) + int((ent.learned_points - ent.potential) / 2))
        for skill in sorted(set(ent.skill_levels) | set(ent.skill_xp)):
            level = int(ent.skill_levels.get(skill, 0))
            xp = int(ent.skill_xp.get(skill, 0))
            if level <= 0 and xp <= 0:
                continue
            level -= 1
            ent.skill_xp.pop(skill, None)
            if level <= 0:
                ent.skill_levels.pop(skill, None)
            else:
                ent.skill_levels[skill] = level
        ent.death_count = int(ent.death_count) + 1
        return

    if op in ("combat_log", "growth_log", "death_log", "aggression_log", "wimpy_log"):
        # 只读战报：rounds[]/成长明细随事件持久化，回放时不改状态。
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

    if op == "shop_hire":
        # 店铺雇佣契约：写入岗位/日薪 + 就业指向，并从原雇主名册里摘除。
        ent = _entity(state, str(d["entity"]))
        bid = str(d["business"])
        biz = state.businesses.get(bid)
        if biz is None:
            raise EffectError(f"商业体不存在: {bid}")
        wage = _as_int(d["wage"], "wage")
        if wage <= 0:
            raise EffectError("日薪必须为正")
        old = ent.employed_at
        if old and old in state.businesses and old != bid:
            prev = state.businesses[old]
            if ent.id in prev.employees:
                prev.employees.remove(ent.id)
            prev.employee_wages.pop(ent.id, None)
            prev.employee_positions.pop(ent.id, None)
        ent.employed_at = bid
        if ent.id not in biz.employees:
            biz.employees.append(ent.id)
        biz.employee_wages[ent.id] = wage
        biz.employee_positions[ent.id] = str(d.get("position", ""))
        return

    if op == "shop_dismiss":
        # 辞退只解除契约，不核销欠薪：欠薪仍以 debtor=店铺 的债务存在，由日界清偿。
        ent = _entity(state, str(d["entity"]))
        bid = str(d["business"])
        biz = state.businesses.get(bid)
        if biz is None:
            raise EffectError(f"商业体不存在: {bid}")
        if ent.id not in biz.employees:
            raise EffectError(f"不是本店雇工: {ent.id}")
        biz.employees.remove(ent.id)
        biz.employee_wages.pop(ent.id, None)
        biz.employee_positions.pop(ent.id, None)
        if ent.employed_at == bid:
            ent.employed_at = None
        return

    if op == "shop_price":
        # 店主定价覆盖：price<=0 表示撤销，恢复基准价。价格本身由动作层校验 ±20%。
        biz = state.businesses.get(str(d["business"]))
        if biz is None:
            raise EffectError(f"商业体不存在: {d['business']}")
        item = str(d["item"])
        price = _as_int(d["price"], "price")
        if price <= 0:
            biz.price_overrides.pop(item, None)
        else:
            biz.price_overrides[item] = price
        return

    if op == "shop_report":
        # 只读视图：不修改 state，由 API 的 _response 透传给玩家。
        return

    if op == "shop_history":
        # 日结快照只留最近 7 条；row 与 system_shop_settle 事件 args 同源。
        biz = state.businesses.get(str(d["business"]))
        if biz is None:
            raise EffectError(f"商业体不存在: {d['business']}")
        row = dict(d.get("row") or {})
        biz.day_history.append(row)
        if len(biz.day_history) > 7:
            biz.day_history = biz.day_history[-7:]
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

    if op == "marriage_meta":
        a, b = str(d["a"]), str(d["b"])
        _entity(state, a)
        _entity(state, b)
        status = str(d.get("status", ""))
        if status not in {"proposed", "accepted", "courtship", "rejected", "married", "ended"}:
            raise EffectError(f"非法婚姻状态: {status}")
        key = f"{a}|{b}" if a <= b else f"{b}|{a}"
        rec = dict(state.marriages.get(key, {}))
        rec["a"], rec["b"] = (a, b) if a <= b else (b, a)
        rec["status"] = status
        if status == "married":
            rec.pop("expires_tick", None)
            rec.pop("ended_tick", None)
        elif status in {"proposed", "accepted", "courtship"}:
            rec.pop("ended_tick", None)
        for f in (
            "since_tick",
            "proposal_tick",
            "decision",
            "reason_code",
            "expires_tick",
            "last_daily_tick",
            "daily_note",
            "gift_money",
            "gift_item",
            "gift_qty",
            "ended_tick",
            "thresholds",
        ):
            if f in d:
                rec[f] = d[f]
        state.marriages[key] = rec
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

    if op == "quest_step":
        # 任务过程化：只接受“接单人本人”的世界状态写入，单位口径由动作层声明。
        qid = str(d["id"])
        quest = state.quests.get(qid)
        if quest is None:
            raise EffectError(f"任务不存在: {qid}")
        actor = str(d["actor"])
        if quest.status != "accepted" or quest.acceptor != actor:
            raise EffectError("只有接单人本人能给该任务记进度")
        units = _as_int(d.get("units", 0), "units")
        if units < 0:
            raise EffectError("quest_step.units 不能为负")
        quest.progress_units += units
        quest.progress_tick = _as_int(d.get("tick", 0), "tick")
        quest.progress_verb = str(d.get("verb", ""))
        quest.progress_note = str(d.get("note", ""))
        return

    if op in ("equip", "unequip"):
        ent = _entity(state, str(d["entity"]))
        slot = str(d["slot"])
        if not slot:
            raise EffectError("装备槽位不能为空")

        def _apply_delta(mapping: Any, sign: int) -> None:
            if not isinstance(mapping, dict):
                return
            for key, value in sorted(mapping.items()):
                if not str(key).startswith("apply/"):
                    continue
                if isinstance(value, bool) or not isinstance(value, int):
                    continue
                total = int(ent.equipped_apply.get(key, 0)) + sign * int(value)
                if total:
                    ent.equipped_apply[str(key)] = total
                else:
                    ent.equipped_apply.pop(str(key), None)

        if op == "equip":
            item = str(d["item"])
            if int(ent.inventory.get(item, 0)) < 1:
                raise EffectError("背包里没有这件物品")
            old = ent.equipped.get(slot)
            if old:
                ent.inventory[old] = int(ent.inventory.get(old, 0)) + 1
                _apply_delta(d.get("old_apply"), -1)
            held = int(ent.inventory.get(item, 0)) - 1
            if held > 0:
                ent.inventory[item] = held
            else:
                ent.inventory.pop(item, None)
            ent.equipped[slot] = item
            _apply_delta(d.get("apply"), 1)
            if slot == "weapon":
                ent.weapon_type = str(d.get("weapon_type") or "")
        else:
            item = ent.equipped.pop(slot, None)
            if item is None:
                raise EffectError("该槽位没有已装备物品")
            ent.inventory[item] = int(ent.inventory.get(item, 0)) + 1
            _apply_delta(d.get("apply"), -1)
            if slot == "weapon":
                ent.weapon_type = ""
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
        # 次级目标（自提案）不接管 entity.goal_id：既有内容主线继续规划，
        # 自目标只靠带 `_goal_id` 的独立额度动作推进。
        if not bool(d.get("secondary", False)):
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

    if op == "player_upsert":
        pid = str(d["id"])
        if not pid.startswith("player:"):
            raise EffectError(f"玩家 id 必须以 player: 开头: {pid}")
        name = str(d.get("name", pid))
        archetype = str(d.get("archetype", ""))
        role = str(d.get("role", "visitor"))
        created = _as_int(d.get("created_tick", 0), "created_tick")
        prof = state.players.get(pid)
        if prof is None:
            state.players[pid] = PlayerProfile(
                id=pid,
                name=name,
                archetype=archetype,
                role=role,
                created_tick=created,
            )
        else:
            # 身份不可中途切换；只允许从创世占位（空/visitor）升级一次。
            if archetype and prof.archetype and archetype != prof.archetype:
                raise EffectError(f"玩家身份已锁定: {prof.archetype} != {archetype}")
            if role and prof.role not in ("", "visitor") and role != prof.role:
                raise EffectError(f"玩家职业已锁定: {prof.role} != {role}")
            if archetype:
                prof.archetype = archetype
            if role:
                prof.role = role
            if name:
                prof.name = name
        return

    if op == "player_endow":
        pid = str(d["id"])
        prof = state.players.get(pid)
        if prof is None:
            raise EffectError(f"玩家档案不存在: {pid}")
        if prof.endowed:
            raise EffectError(f"玩家已发放初始资产: {pid}")
        amount = _as_int(d["amount"], "amount")
        if amount <= 0:
            raise EffectError("初始资产金额必须为正")
        source = str(d.get("source", "")).strip()
        if not source:
            raise EffectError("初始资产必须写明来源")
        tick = _as_int(d.get("tick", 0), "tick")
        prof.endowed = True
        prof.endowed_tick = tick
        prof.endowed_source = source
        # 只记账不铸币：货币由同事件的显式 mint 产生，这里只累计可审计总额。
        state.endowment_total += amount
        return

    if op == "player_presence":
        pid = str(d["id"])
        prof = state.players.get(pid)
        if prof is None:
            raise EffectError(f"玩家档案不存在: {pid}")
        presence = str(d["presence"])
        if presence not in PLAYER_PRESENCE_STATES:
            raise EffectError(f"非法在线状态: {presence}")
        tick = _as_int(d.get("tick", 0), "tick")
        prof.presence = presence
        prof.online = presence == "online"
        # 只有在线/心跳才刷新意图 tick；离线降级不能重置计时，否则永远停留 offline。
        if presence == "online":
            prof.last_intent_tick = max(prof.last_intent_tick, tick)
        if presence == "offline":
            prof.offline_since_tick = tick
        elif presence == "custody":
            prof.custody_since_tick = tick
        elif presence == "dormant":
            prof.dormant_since_tick = tick
        ent = state.entities.get(pid)
        if ent is None:
            raise EffectError(f"玩家实体不存在: {pid}")
        ent.presence = presence
        ent.last_seen_tick = tick
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
