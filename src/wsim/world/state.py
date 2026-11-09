"""世界状态（投影）。

内存状态只是事件日志的投影：所有字段都由 Effect 变更，可由 Replay 重建。
本模块不提供任何 IO，也不读取墙钟。
关系是有向的（a -> b），便于 K6 检测单向强依赖；婚姻会写入双向关系。
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from typing import Any

from ..core.canonical import hash_obj
from ..core.models import ScheduleItem


@dataclass
class Entity:
    id: str
    name: str
    role: str
    location: str
    home: str = ""
    stamina: float = 100.0
    max_stamina: float = 100.0
    status: str = "alive"  # alive / unconcious / dead / reincarnated
    inventory: dict[str, int] = field(default_factory=dict)
    # 已装备槽位 → 物品 id。空字典不写进 to_dict()，确保无玩家世界哈希不变。
    equipped: dict[str, str] = field(default_factory=dict)
    # 已装备物品的 apply 合计（`apply/armor` 等）。只由 equip/unequip 效果维护，
    # 空字典不写进 to_dict()：旧世界没有装备事件，哈希不变。
    equipped_apply: dict[str, int] = field(default_factory=dict)
    # 手持兵器的技能类型（sword/blade/...，来自物品 equip_slot）。
    weapon_type: str = ""
    # NPC 随身装备（内容包 `equipment` 原样，可重复）。死亡时转成地面物品；
    # 旧九地点 NPC 没有该字段，空列表不写进 to_dict()，哈希不变。
    equipment: list[str] = field(default_factory=list)
    # 原版 `die()` 里 `new(__DIR__ "obj/...")` 的固定掉落（内容包 `drops[]`，
    # 只收 chance=1 / item_resolved / landing=ground）。旧内容没有该字段，
    # 空字典不写进 to_dict()，旧世界哈希不变。
    drops: dict[str, int] = field(default_factory=dict)
    skills: dict[str, float] = field(default_factory=dict)
    personality: dict[str, Any] = field(default_factory=dict)
    employed_at: str | None = None
    spouse: str | None = None
    master: str | None = None
    disciples: list[str] = field(default_factory=list)
    business: str | None = None
    faction: str | None = None
    goal_id: str | None = None
    escort_id: str | None = None
    last_action_tick: int = 0
    # 玩家在场状态（P3-C）：online / offline。NPC 默认 online 且不写哈希。
    presence: str = "online"
    last_seen_tick: int = 0
    # ---- 战斗属性（xkx parity，P2）----
    # 全部为整数；旧内容包不提供这些键时保持默认值，且 to_dict() 不输出，
    # 保证无战斗实体的旧世界哈希逐字节不变。`skills` 的 0..1 浮点语义保持不变，
    # 战斗读取时回退为 int(skills[skill] * 100)；新内容使用 skill_levels/skill_xp。
    hp: int = 0
    max_hp: int = 0
    # 原版 `eff_qi`（伤势天花板）：0/-1 都是合法值，用 -2 表示"旧实体未初始化"，
    # 运行时按 `max_hp` 处理；旧世界/未受伤实体不写进 to_dict()，哈希不变。
    eff_hp: int = -2
    # 被动心跳恢复（原版 heal_up）：credit 单位 1/10000 tick，cd 为剩余心跳数。
    heal_credit: int = 0
    heal_cd: int = 0
    # 昏迷苏醒 tick（原版 `call_out("revive", random(100-con)+30)`）。
    revive_tick: int = 0
    attack: int = 0  # 基础伤害（兵器/拳脚），0 视为 1
    defense: int = 0  # 护甲减伤
    # 原版 `set_temp("apply/...")` 的临时攻防（内容 `temp_apply[]` 中 known=true 的
    # 整数项）。键为 `apply/attack` / `apply/defense` / `apply/armor` /
    # `apply/damage`；旧内容没有该字段，空字典不写进 to_dict()，哈希不变。
    temp_apply: dict[str, int] = field(default_factory=dict)
    attrs: dict[str, int] = field(default_factory=dict)  # str/int/con/dex/sta/spi/kar/cps/cor
    combat_exp: int = 0
    # 动态任务奖励的潜力/声望（原版 questd `pot` / `score`）。旧内容不发放，
    # 为 0 时不写进 to_dict()，旧世界哈希逐字节不变。
    potential: int = 0
    score: int = 0
    skill_levels: dict[str, int] = field(default_factory=dict)  # 技能等级（0..800）
    # 技能学习点数：课程/练习按原版 `improve_skill` 的 (level+1)^2 阈值累积；
    # 战斗 tick 的 skill_xp_add 仍沿用 100 点一级的旧语义。
    skill_xp: dict[str, int] = field(default_factory=dict)
    # 原版 `learned_points`：学技能时按潜能差额记账（死亡清零）。
    learned_points: int = 0
    # 原版 query_skill_mapped(type)：基础技能 → 具体武功（`skills_actions.json` 的招式归属）。
    # 旧内容不提供，空字典不写进 to_dict()，旧世界哈希不变。
    map_skills: dict[str, str] = field(default_factory=dict)
    # 原版 query_skill_prepare() 的键（保留；当前战斗只读 map_skills）。
    prepare_skills: list[str] = field(default_factory=list)
    # 原版种族（`set("race", ...)`）；无映射技能时用它取 `combat_action` 默认招式。
    race: str = ""
    # 内功发力（`force.lpc hit_ob`）：neili 当前内力、jiali 加力、max_neili 上限。
    # 全部为 0 时不写进 to_dict()。
    neili: int = 0
    max_neili: int = 0
    jiali: int = 0
    death_tick: int = 0
    death_location: str = ""
    death_count: int = 0
    # 房间 reset 重生时保留的上一次死亡审计线索（原版 destruct + new()
    # 会丢失对象级历史，我们额外留痕）。非 0 才写进 to_dict()。
    last_death_tick: int = 0
    last_death_location: str = ""
    # 同模板多实例：额外实例指向规范模板 id 与本房间槽位（从 1 起）。
    # 规范实例留空/0，to_dict() 不输出，旧世界哈希逐字节不变。
    template: str = ""
    instance: int = 0
    # NPC 主动攻击交战状态（原版 `enemy` / `killer` + 心跳）。
    # 只在 xkx 主动攻击真正发生时写入；空值不输出，旧九地点哈希逐字节不变。
    combat_target: str = ""
    combat_tick: int = 0
    combat_round: int = 0
    # 原版 `init()` 不直接 `kill_ob`，而是 `call_out("start_aggressive", 0)` 或
    # `call_out("kill_ob", 1)`：此字段是延迟生效 tick，0 表示没有待生效的登记。
    # 待生效期间尚未写 `combat_target`，因此 `touxi`/`ansuan` 仍可偷袭（忠实原版窗口）。
    aggro_tick: int = 0
    aggro_target: str = ""
    # 多敌敌人表（原版 `enemy` 数组）：登记顺序、按 id 去重，最多参与 `random(4)` 窗口。
    # `combat_target` 是最近一次选中的主目标；空表不输出，旧世界哈希不变。
    combat_enemies: list[str] = field(default_factory=list)
    # 该实体实际被扣气血按攻击者累计（审计用，不是仇恨权重，不参与选敌）。
    combat_contrib: dict[str, int] = field(default_factory=dict)
    # 原版 `env/wimpy`：当「气」百分比低于该值时自动尝试逃跑（0 为关闭）。
    # 只在玩家显式设置后写入；0 不输出，旧世界哈希逐字节不变。
    wimpy: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "location": self.location,
            "home": self.home,
            "stamina": self.stamina,
            "max_stamina": self.max_stamina,
            "status": self.status,
            "inventory": self.inventory,
            "skills": self.skills,
            "personality": self.personality,
            "employed_at": self.employed_at,
            "spouse": self.spouse,
            "master": self.master,
            "disciples": self.disciples,
            "business": self.business,
            "faction": self.faction,
            "goal_id": self.goal_id,
            "escort_id": self.escort_id,
            "last_action_tick": self.last_action_tick,
        }
        # 只在非默认值时出现，避免旧 run 的世界哈希被新字段改写。
        if self.presence != "online":
            data["presence"] = self.presence
        if self.last_seen_tick:
            data["last_seen_tick"] = self.last_seen_tick
        if self.equipped:
            data["equipped"] = dict(sorted(self.equipped.items()))
        if self.equipped_apply:
            data["equipped_apply"] = dict(sorted(self.equipped_apply.items()))
        if self.weapon_type:
            data["weapon_type"] = self.weapon_type
        if self.equipment:
            data["equipment"] = list(self.equipment)
        if self.drops:
            data["drops"] = dict(sorted(self.drops.items()))
        # 战斗字段：只在内容包显式提供后出现，旧世界（max_hp=0）不新增任何键。
        if self.max_hp:
            data["hp"] = int(self.hp)
            data["max_hp"] = int(self.max_hp)
            # 只有"真受过伤"才写 eff_qi：未初始化(-2)与未受伤(==max)都不写，
            # 保证旧世界与健康实体零新增字段。
            if self.eff_hp >= -1 and int(self.eff_hp) != int(self.max_hp):
                data["eff_hp"] = int(self.eff_hp)
                if self.heal_credit:
                    data["heal_credit"] = int(self.heal_credit)
                if self.heal_cd:
                    data["heal_cd"] = int(self.heal_cd)
        if self.revive_tick:
            data["revive_tick"] = int(self.revive_tick)
        if self.attack:
            data["attack"] = int(self.attack)
        if self.defense:
            data["defense"] = int(self.defense)
        if self.temp_apply:
            data["temp_apply"] = dict(sorted(self.temp_apply.items()))
        if self.attrs:
            data["attrs"] = dict(sorted(self.attrs.items()))
        if self.combat_exp:
            data["combat_exp"] = int(self.combat_exp)
        if self.potential:
            data["potential"] = int(self.potential)
        if self.score:
            data["score"] = int(self.score)
        if self.skill_levels:
            data["skill_levels"] = dict(sorted(self.skill_levels.items()))
        if self.skill_xp:
            data["skill_xp"] = dict(sorted(self.skill_xp.items()))
        if self.map_skills:
            data["map_skills"] = dict(sorted(self.map_skills.items()))
        if self.prepare_skills:
            data["prepare_skills"] = list(self.prepare_skills)
        if self.race:
            data["race"] = self.race
        if self.neili:
            data["neili"] = int(self.neili)
        if self.max_neili:
            data["max_neili"] = int(self.max_neili)
        if self.jiali:
            data["jiali"] = int(self.jiali)
        if self.death_tick or self.death_location:
            data["death_tick"] = int(self.death_tick)
            data["death_location"] = self.death_location
        if self.last_death_tick or self.last_death_location:
            data["last_death_tick"] = int(self.last_death_tick)
            data["last_death_location"] = self.last_death_location
        if self.learned_points:
            data["learned_points"] = int(self.learned_points)
        if self.death_count:
            data["death_count"] = int(self.death_count)
        if self.template:
            data["template"] = self.template
            data["instance"] = int(self.instance)
        if self.combat_target:
            data["combat_target"] = self.combat_target
            data["combat_tick"] = int(self.combat_tick)
            data["combat_round"] = int(self.combat_round)
        # 只在延迟登记窗口内出现；生效后清零，不残留到旧世界/健康实体。
        if self.aggro_tick:
            data["aggro_tick"] = int(self.aggro_tick)
            data["aggro_target"] = self.aggro_target
        if self.combat_enemies:
            data["combat_enemies"] = list(self.combat_enemies)
        if self.combat_contrib:
            data["combat_contrib"] = dict(sorted(self.combat_contrib.items()))
        if self.wimpy:
            data["wimpy"] = int(self.wimpy)
        return data


@dataclass
class PlayerProfile:
    """玩家账号档案（P3-E）。

    钱包/物品/技能/关系仍存在 Entity / accounts / relations 里，本类只保存
    账号级身份与在线生命周期。`players` 为空时 `to_dict()` 不输出该键，
    保证 P3-D 及更早的无玩家 run 哈希不变。
    """

    id: str
    name: str
    archetype: str
    role: str
    online: bool = False
    presence: str = "offline"  # online / offline / custody / dormant
    created_tick: int = 0
    last_intent_tick: int = 0
    offline_since_tick: int = 0
    custody_since_tick: int = 0
    dormant_since_tick: int = 0
    # 首次进入的一次性初始资产（P3-E P0）。按 account_id 幂等：
    # endowed=True 后无论 enter/leave/重进都不再发放。
    endowed: bool = False
    endowed_tick: int = 0
    endowed_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "archetype": self.archetype,
            "role": self.role,
            "online": self.online,
            "presence": self.presence,
            "created_tick": self.created_tick,
            "last_intent_tick": self.last_intent_tick,
            "offline_since_tick": self.offline_since_tick,
            "custody_since_tick": self.custody_since_tick,
            "dormant_since_tick": self.dormant_since_tick,
            "endowed": self.endowed,
            "endowed_tick": self.endowed_tick,
            "endowed_source": self.endowed_source,
        }


@dataclass
class Business:
    id: str
    name: str
    location: str
    kind: str
    owner: str | None = None
    cash_account: str = ""
    stock: dict[str, int] = field(default_factory=dict)
    employees: list[str] = field(default_factory=list)
    # 雇工契约（仅玩家店铺使用）：岗位与日薪。空字典不写进 state 字典，
    # 保证没有雇工的旧世界哈希逐字节不变。
    employee_wages: dict[str, int] = field(default_factory=dict)
    employee_positions: dict[str, str] = field(default_factory=dict)
    # 日结运行期计数（仅玩家店铺累计，由 transfer 效果维护）：当天顾客买入与进货支出。
    # 刻意不写进 to_dict()：没有玩家店铺的旧世界 state hash 必须逐字节不变。
    day_revenue: int = 0
    day_restock: int = 0
    # 欠缴日摊税（运行期，不进 to_dict）：现金不足时结转到次日，仍由
    # system_shop_settle 自己补缴，不混入 system_debts。
    tax_arrears: int = 0
    # 当日成交笔数（运行期，不进 to_dict）：shop_report 的 tx 与容量判断共用。
    day_customers: int = 0
    # 店主自定价（相对基准价 ±20% 内）：空字典不写进 to_dict()，旧世界哈希不变。
    price_overrides: dict[str, int] = field(default_factory=dict)
    # 最近 7 个世界日的日结快照：空列表不写进 to_dict()，无玩家店世界零污染。
    day_history: list[dict[str, Any]] = field(default_factory=list)
    # 出勤的持久凭证（仅玩家店铺、NPC 日口径）：最近一次带 employer 的到店 NPC 日。
    # 记忆列表上限 80 条且按时间淘汰，不能作为出勤证据；-1 = 尚未出勤。
    attendance_day: int = -1

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "name": self.name,
            "location": self.location,
            "kind": self.kind,
            "owner": self.owner,
            "cash_account": self.cash_account,
            "stock": self.stock,
            "employees": self.employees,
        }
        if self.employee_wages:
            data["employee_wages"] = dict(sorted(self.employee_wages.items()))
            data["employee_positions"] = dict(sorted(self.employee_positions.items()))
        if self.price_overrides:
            data["price_overrides"] = dict(sorted(self.price_overrides.items()))
        if self.day_history:
            data["day_history"] = list(self.day_history)
        if self.attendance_day >= 0:
            data["attendance_day"] = int(self.attendance_day)
        return data


@dataclass
class Faction:
    id: str
    name: str
    location: str
    founder: str
    treasury_account: str
    members: list[str] = field(default_factory=list)
    leader: str | None = None
    # 门派日结的持久凭证（NPC 日）：记忆会被 80 条上限淘汰，不能作为幂等依据。
    settle_day: int = -1

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "name": self.name,
            "location": self.location,
            "founder": self.founder,
            "treasury_account": self.treasury_account,
            "members": self.members,
            "leader": self.leader,
        }
        if self.settle_day >= 0:
            data["settle_day"] = int(self.settle_day)
        return data


@dataclass
class Relation:
    a: str
    b: str
    kind: str
    affection: float = 0.0
    trust: float = 0.4
    debt: int = 0
    status: str = "active"

    @property
    def key(self) -> str:
        return f"{self.a}|{self.b}|{self.kind}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "a": self.a,
            "b": self.b,
            "kind": self.kind,
            "affection": self.affection,
            "trust": self.trust,
            "debt": self.debt,
            "status": self.status,
        }


@dataclass
class Debt:
    id: str
    creditor: str
    debtor: str
    amount: int
    long_term: bool = False
    status: str = "active"  # active / settled
    created_tick: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "creditor": self.creditor,
            "debtor": self.debtor,
            "amount": self.amount,
            "long_term": self.long_term,
            "status": self.status,
            "created_tick": self.created_tick,
            "note": self.note,
        }


@dataclass
class EscortOrder:
    id: str
    origin: str
    destination: str
    cargo_value: int
    fee: int
    deposit: int
    status: str = "open"  # open / active / done / failed
    carrier: str | None = None
    success: bool = False
    arrive_tick: int = 0
    created_tick: int = 0
    route_id: str = ""
    agency_cut: float = 0.0
    fee_penalty: int = 0
    cargo_loss_ratio: float = 0.0
    risk_event: str = ""
    risk_stamina: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "origin": self.origin,
            "destination": self.destination,
            "cargo_value": self.cargo_value,
            "fee": self.fee,
            "deposit": self.deposit,
            "status": self.status,
            "carrier": self.carrier,
            "success": self.success,
            "arrive_tick": self.arrive_tick,
            "created_tick": self.created_tick,
            "route_id": self.route_id,
            "agency_cut": self.agency_cut,
            "fee_penalty": self.fee_penalty,
            "cargo_loss_ratio": self.cargo_loss_ratio,
            "risk_event": self.risk_event,
            "risk_stamina": self.risk_stamina,
        }


@dataclass
class Quest:
    id: str
    template_id: str
    giver: str
    acceptor: str | None
    target_npc: str
    target_location: str
    verb: str
    args: dict[str, Any]
    reward_money: int
    reward_relation: float
    deadline_tick: int
    status: str = "open"  # open / accepted / completed / failed
    created_tick: int = 0
    accepted_tick: int = -1
    completed_tick: int = -1
    settlement_amount: int = 0
    # 任务过程化（P4）：只有玩家接单并真实完成目标动作后才写入，且仅在非默认时
    # 序列化，保证无玩家世界的 state hash 逐字节不变。units 的口径：
    # work=累计工时、buy=累计购入件数、其它动作=1（一次即算完成）。
    progress_units: int = 0
    progress_tick: int = -1
    progress_verb: str = ""
    progress_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "template_id": self.template_id,
            "giver": self.giver,
            "acceptor": self.acceptor,
            "target_npc": self.target_npc,
            "target_location": self.target_location,
            "verb": self.verb,
            "args": self.args,
            "reward_money": self.reward_money,
            "reward_relation": self.reward_relation,
            "deadline_tick": self.deadline_tick,
            "status": self.status,
            "created_tick": self.created_tick,
            "accepted_tick": self.accepted_tick,
            "completed_tick": self.completed_tick,
            "settlement_amount": self.settlement_amount,
        }
        if self.progress_tick >= 0 or self.progress_units:
            data["progress"] = {
                "units": self.progress_units,
                "tick": self.progress_tick,
                "verb": self.progress_verb,
                "note": self.progress_note,
            }
        return data


@dataclass
class Goal:
    id: str
    npc: str
    kind: str
    target: float
    progress: float = 0.0
    status: str = "active"  # active / completed / abandoned
    created_tick: int = 0
    source: str = "npc"
    content_goal_id: str = ""
    content_type: str = ""
    progress_log: list[tuple[int, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "npc": self.npc,
            "kind": self.kind,
            "target": self.target,
            "progress": self.progress,
            "status": self.status,
            "created_tick": self.created_tick,
            "source": self.source,
            "content_goal_id": self.content_goal_id,
            "content_type": self.content_type,
            "progress_log": [list(x) for x in self.progress_log],
        }


@dataclass
class WorldState:
    locations: dict[str, dict[str, Any]] = field(default_factory=dict)
    tick: int = 0
    entities: dict[str, Entity] = field(default_factory=dict)
    players: dict[str, PlayerProfile] = field(default_factory=dict)
    businesses: dict[str, Business] = field(default_factory=dict)
    factions: dict[str, Faction] = field(default_factory=dict)
    relations: dict[str, Relation] = field(default_factory=dict)
    debts: dict[str, Debt] = field(default_factory=dict)
    escorts: dict[str, EscortOrder] = field(default_factory=dict)
    quests: dict[str, Quest] = field(default_factory=dict)
    goals: dict[str, Goal] = field(default_factory=dict)
    accounts: dict[str, int] = field(default_factory=dict)
    schedule: dict[str, ScheduleItem] = field(default_factory=dict)
    memories: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # 玩家对话的关系增量台账：npc -> {day, day_affection, day_trust,
    # last_tick, tick_affection, tick_trust}。用于 ±2/tick、±8/日 上限；
    # 对话轮次由 NPC memory 的 talk:npc 条数推导，不在此新增字段。
    talk_ledger: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 任务模板历史统计：template_id -> {created, completed, failed, expired}。
    quest_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    quests_expired_total: int = 0
    minted_total: int = 0
    burned_total: int = 0
    # 玩家初始资产累计发放额（含创世预置与运行中首次进入）。它是 minted_total
    # 的子集，只用于审计“钱从哪来”；为 0 时不写入哈希，旧 run 不受影响。
    endowment_total: int = 0
    items_created: dict[str, int] = field(default_factory=dict)
    items_burned: dict[str, int] = field(default_factory=dict)
    # 服务端审计（如 spoofed_arg）。空列表不写入哈希，保证旧 run 可回放。
    audit_log: list[dict[str, Any]] = field(default_factory=list)
    # 玩家婚姻元数据：键为排序后的 "a|b"。只有至少一方是玩家时才写入；
    # 为空时不影响哈希，保证无玩家世界逐字节不变。
    marriages: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 地面掉落：location -> {item_id: qty}。所有写入只经 loot_create / loot_take
    # 两个 Effect；为空时不写进 to_dict()，旧世界哈希逐字节不变。
    ground: dict[str, dict[str, int]] = field(default_factory=dict)
    # 地面物品的击杀来源分桶：location -> [{key, source, entity, tick, items}]。
    # 只由 loot_create 的 source="kill" 写入；`loot` 只取最近一次击杀条目，
    # 旧事件没有来源字段 → 这里为空 → `loot` 视为无击杀掉落。为空时不写进
    # to_dict()，旧世界哈希逐字节不变。
    ground_sources: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # xkx 任务进度：键为 "actor|quest_id"。原版支线只用 NPC `set_temp` 一次性
    # 开关、没有持久完成标记；这里把进度写进世界状态，保证可回放。为空时
    # 不写进 to_dict()，旧九地点世界哈希逐字节不变。
    xkx_quests: dict[str, dict[str, Any]] = field(default_factory=dict)

    def clone(self) -> "WorldState":
        """整世界深拷贝（写事务的 trial 副本）。

        WorldState 只含纯数据（dataclass/dict/list/元组/标量），pickle 往返在
        CPython 上比逐字段 deepcopy 快约 2 倍，且天然保持值语义；写入失败时
        旧投影不受影响。若将来状态里混入不可 pickle 的对象，这里会直接报错，
        而不是产生半拷贝的别名状态。
        """
        return pickle.loads(pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL))

    def to_dict(self) -> dict[str, Any]:
        data = {
            "entities": {k: v.to_dict() for k, v in sorted(self.entities.items())},
            "businesses": {k: v.to_dict() for k, v in sorted(self.businesses.items())},
            "factions": {k: v.to_dict() for k, v in sorted(self.factions.items())},
            "relations": {k: v.to_dict() for k, v in sorted(self.relations.items())},
            "debts": {k: v.to_dict() for k, v in sorted(self.debts.items())},
            "escorts": {k: v.to_dict() for k, v in sorted(self.escorts.items())},
            "quests": {k: v.to_dict() for k, v in sorted(self.quests.items())},
            "goals": {k: v.to_dict() for k, v in sorted(self.goals.items())},
            "accounts": dict(sorted(self.accounts.items())),
            "schedule": {k: v.to_dict() for k, v in sorted(self.schedule.items())},
            "memories": {k: v for k, v in sorted(self.memories.items())},
            "talk_ledger": {k: dict(sorted(v.items())) for k, v in sorted(self.talk_ledger.items())},
            "quest_stats": {
                k: dict(sorted(v.items())) for k, v in sorted(self.quest_stats.items())
            },
            "quests_expired_total": self.quests_expired_total,
            "minted_total": self.minted_total,
            "burned_total": self.burned_total,
            "items_created": dict(sorted(self.items_created.items())),
            "items_burned": dict(sorted(self.items_burned.items())),
        }
        if self.players:
            data["players"] = {k: v.to_dict() for k, v in sorted(self.players.items())}
        if self.endowment_total:
            data["endowment_total"] = self.endowment_total
        if self.audit_log:
            data["audit_log"] = list(self.audit_log)
        if self.marriages:
            data["marriages"] = {
                k: dict(sorted(v.items())) for k, v in sorted(self.marriages.items())
            }
        if self.ground:
            data["ground"] = {
                k: dict(sorted(v.items())) for k, v in sorted(self.ground.items()) if v
            }
        if self.ground_sources:
            data["ground_sources"] = {
                loc: [
                    {
                        "key": str(entry.get("key") or ""),
                        "source": str(entry.get("source") or ""),
                        "entity": str(entry.get("entity") or ""),
                        "tick": int(entry.get("tick") or 0),
                        "items": dict(sorted((entry.get("items") or {}).items())),
                    }
                    for entry in entries
                ]
                for loc, entries in sorted(self.ground_sources.items())
                if entries
            }
        if self.xkx_quests:
            data["xkx_quests"] = {
                k: dict(sorted(v.items())) for k, v in sorted(self.xkx_quests.items())
            }
        return data

    def hash(self) -> str:
        return hash_obj(self.to_dict())

    def full_dict(self) -> dict[str, Any]:
        """完整状态快照：补上 to_dict 漏掉的 tick 与地点图（含出口）。

        插件隔离必须比较完整状态，否则恶意插件改 tick / 地点连接不会被发现。
        """
        data = self.to_dict()
        data["tick"] = self.tick
        data["locations"] = {
            lid: {
                "id": loc.get("id", lid),
                "name": loc.get("name", ""),
                "exits": sorted(str(x) for x in (loc.get("exits") or [])),
                "services": sorted(str(x) for x in (loc.get("services") or [])),
            }
            for lid, loc in sorted(self.locations.items())
        }
        return data

    def full_hash(self) -> str:
        return hash_obj(self.full_dict())

    def alive_entities(self) -> list[Entity]:
        return sorted((e for e in self.entities.values() if e.status == "alive"), key=lambda e: e.id)

    def business_at(self, location: str) -> list[Business]:
        return sorted((b for b in self.businesses.values() if b.location == location), key=lambda b: b.id)

    def relation(self, a: str, b: str, kind: str) -> Relation | None:
        return self.relations.get(f"{a}|{b}|{kind}")

    def affection_between(self, a: str, b: str) -> float:
        vals = []
        for kind in ("friend", "family", "master", "disciple", "marriage", "apprentice", "ally", "acquaintance"):
            rab = self.relation(a, b, kind)
            rba = self.relation(b, a, kind)
            if rab is not None:
                vals.append(rab.affection)
            if rba is not None:
                vals.append(rba.affection)
        return max(vals) if vals else 0.0

    def account_balance(self, account: str) -> int:
        return int(self.accounts.get(account, 0))
