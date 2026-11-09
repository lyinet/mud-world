"""动作注册表。内置 verb + `plugins/actions/` 下的插件 verb。

插件只在启动时扫描注册；新增插件不需要改本文件或任何 src/ 代码。
"""
from __future__ import annotations

from pathlib import Path

from ..core.models import Effect, Intent
from ..world.state import WorldState


class Action:
    verb: str = ""
    # 同一动作的别名 verb（例如 loot / get）。别名共用同一个实例与 Effect 语义。
    aliases: tuple[str, ...] = ()

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        return []

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        raise NotImplementedError


class ActionRegistry:
    def __init__(self):
        self._actions: dict[str, Action] = {}

    def register(self, action: Action) -> None:
        if not action.verb:
            raise ValueError("action.verb 不能为空")
        for verb in (action.verb, *getattr(action, "aliases", ())):
            self._actions[str(verb)] = action

    def get(self, verb: str) -> Action | None:
        return self._actions.get(verb)

    def verbs(self) -> list[str]:
        # 别名不重复出现在动作清单里；get("get") 仍可正常取到 LootAction。
        return sorted({action.verb for action in self._actions.values()})

    def has(self, verb: str) -> bool:
        return verb in self._actions


def build_registry(
    content,
    seed: int,
    plugin_dir: Path | None = None,
    spawns: list[dict] | None = None,
    room_reset=None,
    initiative_gate: bool = False,
    lethal_fight: bool = False,
    eff_qi: bool = False,
    jing: bool = False,
) -> ActionRegistry:
    from .aggression import NpcAggressionAction
    from .basic import ConsumeAction, CultivateAction, MoveAction, WorkAction
    from .combat import AnsuanAction, FightAction, KillAction, SystemSpawnAction, TouxiAction
    from .business import (
        DismissAction,
        FoundSectAction,
        OpenShopAction,
        RestockAction,
        SetPriceAction,
        ShopReportAction,
        WithdrawAction,
    )
    from .commerce import BuyAction, SellAction, TradeAction
    from .equipment import (
        EquipAction,
        RemoveAction,
        UnequipAction,
        UnwieldAction,
        UseItemAction,
        WearAction,
        WieldAction,
    )
    from .escort import EscortAction, SystemEscortArriveAction
    from .loot import DropAction, LootAction
    from .player import (
        PlayerEndowmentAction,
        PlayerEnterAction,
        PlayerHeartbeatAction,
        PlayerLeaveAction,
        PlayerPresenceAction,
        PlayerRestAction,
        SystemPlayerPresenceAction,
        SystemTickAction,
    )
    from .milestones import SystemMaintenanceAction, SystemMilestoneAction
    from .marriage import (
        MarriageAction,
        MarriageAcceptAction,
        MarriageEndAction,
        MarriageProposeAction,
        MarriageRejectAction,
        SpouseTimeAction,
    )
    from .social import ApprenticeAction, JoinSectAction, MarryAction, RecruitAction, TalkAction
    from .system import (
        SystemBudgetAction,
        SystemDebtsAction,
        SystemGoalAction,
        SystemHealAction,
        SystemLivingAction,
        SystemRecoverAction,
        SystemSavingsTaxAction,
        SystemShopDividendAction,
        SystemShopSettleAction,
        SystemShopTaxAction,
        SystemTreasuryGuardAction,
    )
    from .quests import (
        QuestAcceptAction,
        QuestDeliverAction,
        SystemQuestFailAction,
        SystemQuestPostAction,
    )
    from .xkx_quests import XkxQuestAcceptAction, XkxQuestDeliverAction
    from .wimpy import WimpyAction

    reg = ActionRegistry()
    reg.register(MoveAction(content))
    reg.register(CultivateAction(content))
    reg.register(WorkAction(content))
    reg.register(ConsumeAction(content))
    reg.register(BuyAction(content))
    reg.register(SellAction(content))
    reg.register(TradeAction(content))
    reg.register(EquipAction(content))
    reg.register(WearAction(content))
    reg.register(WieldAction(content))
    reg.register(RemoveAction(content))
    reg.register(UnwieldAction(content))
    reg.register(UnequipAction(content))
    reg.register(UseItemAction(content))
    reg.register(ApprenticeAction(content))
    reg.register(JoinSectAction(content))
    reg.register(MarryAction(content))
    reg.register(MarriageProposeAction(content))
    reg.register(MarriageAcceptAction())
    reg.register(MarriageRejectAction())
    reg.register(MarriageAction())
    reg.register(MarriageEndAction())
    reg.register(SpouseTimeAction(content))
    reg.register(RecruitAction(content))
    talk = TalkAction(content)
    reg.register(talk)
    reg.register(OpenShopAction(content))
    reg.register(RestockAction(content))
    reg.register(SetPriceAction(content))
    reg.register(DismissAction(content))
    reg.register(WithdrawAction(content))
    reg.register(ShopReportAction(content))
    reg.register(EscortAction(content))
    reg.register(FoundSectAction(content))
    reg.register(QuestAcceptAction(content))
    reg.register(QuestDeliverAction(content))
    reg.register(XkxQuestAcceptAction(content))
    reg.register(XkxQuestDeliverAction(content))
    reg.register(SystemQuestPostAction(content))
    reg.register(SystemQuestFailAction(content))
    reg.register(SystemGoalAction(content))
    reg.register(SystemMilestoneAction(content))
    reg.register(SystemMaintenanceAction(content))
    reg.register(SystemRecoverAction(content))
    reg.register(SystemHealAction(content))
    reg.register(SystemLivingAction(content))
    reg.register(SystemDebtsAction(content))
    reg.register(SystemSavingsTaxAction(content))
    reg.register(SystemShopDividendAction(content))
    reg.register(SystemShopSettleAction(content))
    reg.register(SystemShopTaxAction(content))
    reg.register(SystemTreasuryGuardAction(content))
    reg.register(SystemBudgetAction(content))
    reg.register(SystemEscortArriveAction(content))
    reg.register(PlayerEnterAction(content, jing=jing))
    reg.register(PlayerEndowmentAction())
    reg.register(PlayerHeartbeatAction())
    reg.register(PlayerPresenceAction())
    reg.register(PlayerLeaveAction())
    reg.register(PlayerRestAction())
    reg.register(SystemPlayerPresenceAction())
    reg.register(SystemTickAction())
    if lethal_fight:
        # 原版语义：fight 是切磋（半血即止），kill 才下死手。
        reg.register(FightAction(initiative_gate=initiative_gate, spar=True, eff_qi=eff_qi))
        reg.register(KillAction(initiative_gate=initiative_gate, eff_qi=eff_qi))
    else:
        # 旧口径：fight 仍按击杀处理，且不注册 kill。
        reg.register(FightAction(initiative_gate=initiative_gate, eff_qi=eff_qi))
    # `touxi` / `ansuan` 是原版独立命令，内部直接 `do_attack()`，与 fight/kill 并存。
    reg.register(TouxiAction(initiative_gate=initiative_gate, eff_qi=eff_qi))
    reg.register(AnsuanAction(initiative_gate=initiative_gate, eff_qi=eff_qi))
    # NPC 主动攻击：规则只从内容包派生；旧九地点内容没有 aggressive/三个专属 id，
    # 规则表为空，动作永远拒绝，事件流不变。
    from ..sim.aggression import aggression_rules

    reg.register(NpcAggressionAction(aggression_rules(content), initiative_gate=initiative_gate, eff_qi=eff_qi))
    reg.register(LootAction(content))
    reg.register(DropAction(content))
    reg.register(WimpyAction())
    reg.register(SystemSpawnAction(spawns))
    if room_reset is not None and getattr(room_reset, "enabled", False):
        from ..sim.room_reset import SystemRoomResetAction

        reg.register(SystemRoomResetAction(room_reset))

    root = Path(__file__).resolve().parents[3]
    # 对话队列不进注册表自动挂载：由会话层（WorldSession）按实际 run 目录注入，
    # 避免按环境变量猜目录、也避免把 decision/intent 槽一起打开。
    pdir = Path(plugin_dir) if plugin_dir is not None else root / "plugins" / "actions"
    if pdir.is_dir():
        from ..plugins.loader import PluginError, load_plugins

        for action in load_plugins(pdir):
            if reg.has(action.verb):
                raise PluginError(f"插件 verb 与内置动作冲突: {action.verb}")
            reg.register(action)
    return reg

