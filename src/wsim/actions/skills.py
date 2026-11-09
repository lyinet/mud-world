"""原版 `cmds/skill/exert.lpc`：运功对抗伤势。

本阶段只实现基础内功的两个通用功能（`combat.healing` 打开时生效）：

- `recover`   —— `kungfu/skill/force/recover.lpc:7-33`：耗内力，把 `qi` 补到 `eff_qi`；
- `regenerate`—— `kungfu/skill/force/regenerate.lpc:7-33`：jing 同构。

门派自疗/他疗（`bibo-shengong/heal` 等 4 门、`hunyuan-yiqi/lifeheal` 等 2 门）
按 2026-11-09 收窄指令不实现；药品与 `heal`/`cure` 指令同样不在本阶段。
"""
from __future__ import annotations

from ..core.models import Effect, Intent
from ..world.state import Entity, WorldState
from .registry import Action

# 原版 `exert <功能>` 的功能名（小写）；本阶段只认这两个。
EXERT_FUNCTIONS = ("recover", "regenerate")


def force_level(ent: Entity) -> int:
    """原版 `query_skill("force")`：内容等级优先，回退旧档的 0..1 熟练度。"""
    levels = getattr(ent, "skill_levels", None) or {}
    if levels.get("force"):
        return int(levels["force"])
    return int(float((getattr(ent, "skills", None) or {}).get("force", 0.0)) * 100)


def _is_fighting(ent: Entity) -> bool:
    return bool(str(ent.combat_target or "")) or bool(ent.combat_enemies)


class ExertAction(Action):
    """`exert|yun <功能>`；只做自身恢复，不接目标参数。"""

    verb = "exert"
    aliases = ("yun",)

    def __init__(self, healing: bool = False):
        self.healing = bool(healing)

    def _gap(self, ent: Entity, func: str) -> int:
        if func == "recover":
            eff = int(ent.eff_hp) if int(ent.eff_hp) >= -1 else int(ent.max_hp)
            return eff - int(ent.hp)
        eff = int(ent.eff_jing) if int(ent.eff_jing) >= -1 else int(ent.max_jing)
        return eff - int(ent.jing)

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        if not self.healing:
            return ["not_available"]
        ent = state.entities.get(intent.actor)
        if ent is None:
            return ["actor_missing"]
        if ent.status == "dead":
            return ["actor_dead"]
        if ent.status == "unconcious":
            return ["actor_unconcious"]
        if int(ent.max_hp) <= 0:
            return ["no_combat_stats"]
        if int(ent.busy_until) > int(intent.tick):
            return ["busy"]
        func = str(intent.args.get("func") or "").strip().lower()
        if func not in EXERT_FUNCTIONS:
            return ["no_such_function"]
        if func == "regenerate" and int(ent.max_jing) <= 0:
            return ["no_combat_stats"]
        # `recover.lpc:13` / `regenerate.lpc:13`：内力不足 20 不能运功。
        if int(ent.neili) < 20:
            return ["insufficient_neili"]
        # 原版 `n = 100*q/force` 需要非零等级。
        if force_level(ent) <= 0:
            return ["not_qualified"]
        # `recover.lpc:17-18` / `regenerate.lpc:17-18`：缺口小于 10 视为无伤。
        if self._gap(ent, func) < 10:
            return ["not_injured"]
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        ent = state.entities[intent.actor]
        func = str(intent.args.get("func") or "").strip().lower()
        q = self._gap(ent, func)
        level = force_level(ent)
        # 原版 `n = 100*q/force`，最少 20；`n` 超过内力时按比例少回一点。
        n = 100 * q // level
        if n < 20:
            n = 20
        neili = int(ent.neili)
        if n > neili:
            q = q * neili // n
            n = neili
        heal_op = "qi_heal" if func == "recover" else "jing_heal"
        effects = [
            Effect("neili_add", {"entity": ent.id, "delta": -n}),
            Effect(heal_op, {"entity": ent.id, "delta": q}),
        ]
        if _is_fighting(ent):
            # 原版只在战斗中 `start_busy(1)`。
            effects.append(Effect("busy_set", {
                "entity": ent.id, "rounds": 1, "tick": int(intent.tick),
            }))
        return effects
