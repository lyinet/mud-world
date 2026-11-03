"""世界状态（投影）。

内存状态只是事件日志的投影：所有字段都由 Effect 变更，可由 Replay 重建。
本模块不提供任何 IO，也不读取墙钟。
关系是有向的（a -> b），便于 K6 检测单向强依赖；婚姻会写入双向关系。
"""
from __future__ import annotations

import copy
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
    status: str = "alive"  # alive / dead / reincarnated
    inventory: dict[str, int] = field(default_factory=dict)
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

    def to_dict(self) -> dict[str, Any]:
        return {
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "location": self.location,
            "kind": self.kind,
            "owner": self.owner,
            "cash_account": self.cash_account,
            "stock": self.stock,
            "employees": self.employees,
        }


@dataclass
class Faction:
    id: str
    name: str
    location: str
    founder: str
    treasury_account: str
    members: list[str] = field(default_factory=list)
    leader: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "location": self.location,
            "founder": self.founder,
            "treasury_account": self.treasury_account,
            "members": self.members,
            "leader": self.leader,
        }


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
        }


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
    businesses: dict[str, Business] = field(default_factory=dict)
    factions: dict[str, Faction] = field(default_factory=dict)
    relations: dict[str, Relation] = field(default_factory=dict)
    debts: dict[str, Debt] = field(default_factory=dict)
    escorts: dict[str, EscortOrder] = field(default_factory=dict)
    goals: dict[str, Goal] = field(default_factory=dict)
    accounts: dict[str, int] = field(default_factory=dict)
    schedule: dict[str, ScheduleItem] = field(default_factory=dict)
    memories: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    minted_total: int = 0
    burned_total: int = 0
    items_created: dict[str, int] = field(default_factory=dict)
    items_burned: dict[str, int] = field(default_factory=dict)

    def clone(self) -> "WorldState":
        return WorldState(
            locations=self.locations,
            tick=self.tick,
            entities=copy.deepcopy(self.entities),
            businesses=copy.deepcopy(self.businesses),
            factions=copy.deepcopy(self.factions),
            relations=copy.deepcopy(self.relations),
            debts=copy.deepcopy(self.debts),
            escorts=copy.deepcopy(self.escorts),
            goals=copy.deepcopy(self.goals),
            accounts=dict(self.accounts),
            schedule=copy.deepcopy(self.schedule),
            memories=copy.deepcopy(self.memories),
            minted_total=self.minted_total,
            burned_total=self.burned_total,
            items_created=dict(self.items_created),
            items_burned=dict(self.items_burned),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": {k: v.to_dict() for k, v in sorted(self.entities.items())},
            "businesses": {k: v.to_dict() for k, v in sorted(self.businesses.items())},
            "factions": {k: v.to_dict() for k, v in sorted(self.factions.items())},
            "relations": {k: v.to_dict() for k, v in sorted(self.relations.items())},
            "debts": {k: v.to_dict() for k, v in sorted(self.debts.items())},
            "escorts": {k: v.to_dict() for k, v in sorted(self.escorts.items())},
            "goals": {k: v.to_dict() for k, v in sorted(self.goals.items())},
            "accounts": dict(sorted(self.accounts.items())),
            "schedule": {k: v.to_dict() for k, v in sorted(self.schedule.items())},
            "memories": {k: v for k, v in sorted(self.memories.items())},
            "minted_total": self.minted_total,
            "burned_total": self.burned_total,
            "items_created": dict(sorted(self.items_created.items())),
            "items_burned": dict(sorted(self.items_burned.items())),
        }

    def hash(self) -> str:
        return hash_obj(self.to_dict())

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
