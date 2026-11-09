"""刷怪（spawn）系统：内容驱动、确定性、空转不写事件。

约定：
- 内容包在 `content/spawns.yaml` 声明 `spawns:` 列表；旧内容包没有该文件 → 空列表。
- 每个 spawn 点对应一个固定 `entity_id`（默认取 `id`），世界首创时 `create_entity`，
  死亡后在 `death_tick + respawn_ticks` 到期时用 `revive` 复活，不重建实体。
- `spawn_effects()` 只返回 Effect；调度层只有“有到期刷新”时才提交 `system_spawn`
  意图。没有 spawns / 全部活着 / 未到期时返回 []，因此不会产生任何事件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.models import Effect


def _read_yaml(path: Path) -> Any:
    try:
        import yaml
    except ImportError:  # pragma: no cover - 运行环境缺 pyyaml 时退化为空
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_spawns(root: Path) -> list[dict[str, Any]]:
    """读取 `root/content/spawns.yaml`；不存在或格式不合法时返回 []。"""
    path = Path(root) / "content" / "spawns.yaml"
    if not path.exists():
        return []
    data = _read_yaml(path) or {}
    raw = data.get("spawns", data) if isinstance(data, dict) else data
    return normalize_spawns(raw if isinstance(raw, list) else [])


def normalize_spawns(raw: list[Any] | None) -> list[dict[str, Any]]:
    """把内容/测试传入的 spawn 声明规范成确定性的 dict 列表（按 id 排序）。"""
    out: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or item.get("entity_id") or "")
        if not sid:
            continue
        spec = {
            "id": sid,
            "entity_id": str(item.get("entity_id") or sid),
            "name": str(item.get("name") or sid),
            "role": str(item.get("role") or "monster"),
            "location": str(item.get("location") or ""),
            "home": str(item.get("home") or item.get("location") or ""),
            "respawn_ticks": max(0, int(item.get("respawn_ticks", 0))),
            "max_hp": max(0, int(item.get("max_hp", 0))),
            "hp": max(0, int(item.get("hp", item.get("max_hp", 0)))),
            "attack": max(0, int(item.get("attack", 0))),
            "defense": max(0, int(item.get("defense", 0))),
            "attrs": {str(k): int(v) for k, v in dict(item.get("attrs") or {}).items()},
            "skills": {str(k): float(v) for k, v in dict(item.get("skills") or {}).items()},
            "skill_levels": {str(k): int(v) for k, v in dict(item.get("skill_levels") or {}).items()},
            "skill_xp": {str(k): int(v) for k, v in dict(item.get("skill_xp") or {}).items()},
            "combat_exp": max(0, int(item.get("combat_exp", 0))),
            "pursuer": bool(item.get("pursuer")),
            "loot": {str(k): max(0, int(v)) for k, v in dict(item.get("loot") or {}).items()},
        }
        out.append(spec)
    out.sort(key=lambda s: s["id"])
    return out


def _due(state: Any, spec: dict[str, Any], tick: int) -> bool:
    ent = state.entities.get(spec["entity_id"])
    if ent is None:
        return bool(spec["location"])
    if ent.status != "dead":
        return False
    return int(tick) >= int(ent.death_tick) + int(spec["respawn_ticks"])


def spawn_due(state: Any, spawns: list[dict[str, Any]] | None, tick: int) -> bool:
    """是否存在到期刷新；调度层用它决定是否提交 `system_spawn` 意图。"""
    return any(_due(state, spec, tick) for spec in (spawns or []))


def spawn_effects(state: Any, spawns: list[dict[str, Any]] | None, tick: int) -> list[Effect]:
    """生成刷怪 Effect；无内容/无到期刷新时严格返回 []（空转不产生事件）。"""
    effects: list[Effect] = []
    for spec in spawns or []:
        if not _due(state, spec, tick):
            continue
        eid = spec["entity_id"]
        if eid not in state.entities:
            payload: dict[str, Any] = {
                "id": eid,
                "name": spec["name"],
                "role": spec["role"],
                "location": spec["location"],
                "home": spec["home"] or spec["location"],
                "stamina": 100.0,
                "max_stamina": 100.0,
                "skills": spec["skills"],
                "max_hp": spec["max_hp"],
                "hp": spec["hp"] if spec["hp"] > 0 else spec["max_hp"],
                "attack": spec["attack"],
                "defense": spec["defense"],
                "attrs": spec["attrs"],
                "combat_exp": spec["combat_exp"],
                "skill_levels": spec["skill_levels"],
                "skill_xp": spec["skill_xp"],
            }
            if spec["pursuer"]:
                payload["pursuer"] = True
            effects.append(Effect(op="create_entity", data=payload))
        else:
            effects.append(
                Effect(
                    op="revive",
                    data={"entity": eid, "hp": spec["hp"] if spec["hp"] > 0 else spec["max_hp"]},
                )
            )
            if spec["location"] and state.entities[eid].location != spec["location"]:
                effects.append(Effect(op="set_location", data={"entity": eid, "to": spec["location"]}))
        effects.append(Effect(op="audit_log", data={"kind": "spawn", "id": spec["id"], "tick": int(tick)}))
    return effects
