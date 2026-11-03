"""动作注册表。内置 verb + `plugins/actions/` 下的插件 verb。

插件只在启动时扫描注册；新增插件不需要改本文件或任何 src/ 代码。
"""
from __future__ import annotations

from pathlib import Path

from ..core.models import Effect, Intent
from ..world.state import WorldState


class Action:
    verb: str = ""

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
        self._actions[action.verb] = action

    def get(self, verb: str) -> Action | None:
        return self._actions.get(verb)

    def verbs(self) -> list[str]:
        return sorted(self._actions)

    def has(self, verb: str) -> bool:
        return verb in self._actions


def build_registry(content, seed: int, plugin_dir: Path | None = None) -> ActionRegistry:
    from .basic import ConsumeAction, CultivateAction, MoveAction, WorkAction
    from .business import FoundSectAction, OpenShopAction
    from .commerce import BuyAction, SellAction, TradeAction
    from .escort import EscortAction, SystemEscortArriveAction
    from .player import (
        PlayerEndowmentAction,
        PlayerEnterAction,
        PlayerHeartbeatAction,
        PlayerLeaveAction,
        PlayerPresenceAction,
        SystemPlayerPresenceAction,
        SystemTickAction,
    )
    from .social import ApprenticeAction, JoinSectAction, MarryAction, RecruitAction, TalkAction
    from .system import (
        SystemBudgetAction,
        SystemDebtsAction,
        SystemGoalAction,
        SystemLivingAction,
        SystemRecoverAction,
        SystemSavingsTaxAction,
        SystemShopDividendAction,
        SystemShopTaxAction,
        SystemTreasuryGuardAction,
    )
    from .quests import (
        QuestAcceptAction,
        QuestDeliverAction,
        SystemQuestFailAction,
        SystemQuestPostAction,
    )

    reg = ActionRegistry()
    reg.register(MoveAction(content))
    reg.register(CultivateAction(content))
    reg.register(WorkAction(content))
    reg.register(ConsumeAction(content))
    reg.register(BuyAction(content))
    reg.register(SellAction(content))
    reg.register(TradeAction(content))
    reg.register(ApprenticeAction(content))
    reg.register(JoinSectAction(content))
    reg.register(MarryAction(content))
    reg.register(RecruitAction(content))
    reg.register(TalkAction(content))
    reg.register(OpenShopAction(content))
    reg.register(EscortAction(content))
    reg.register(FoundSectAction(content))
    reg.register(QuestAcceptAction(content))
    reg.register(QuestDeliverAction(content))
    reg.register(SystemQuestPostAction(content))
    reg.register(SystemQuestFailAction(content))
    reg.register(SystemGoalAction(content))
    reg.register(SystemRecoverAction(content))
    reg.register(SystemLivingAction(content))
    reg.register(SystemDebtsAction(content))
    reg.register(SystemSavingsTaxAction(content))
    reg.register(SystemShopDividendAction(content))
    reg.register(SystemShopTaxAction(content))
    reg.register(SystemTreasuryGuardAction(content))
    reg.register(SystemBudgetAction(content))
    reg.register(SystemEscortArriveAction(content))
    reg.register(PlayerEnterAction())
    reg.register(PlayerEndowmentAction())
    reg.register(PlayerHeartbeatAction())
    reg.register(PlayerPresenceAction())
    reg.register(PlayerLeaveAction())
    reg.register(SystemPlayerPresenceAction())
    reg.register(SystemTickAction())

    # 规则层：扫描插件目录。插件 verb 不得覆盖内核内置 verb。
    root = Path(__file__).resolve().parents[3]
    pdir = Path(plugin_dir) if plugin_dir is not None else root / "plugins" / "actions"
    if pdir.is_dir():
        from ..plugins.loader import PluginError, load_plugins

        for action in load_plugins(pdir):
            if reg.has(action.verb):
                raise PluginError(f"插件 verb 与内置动作冲突: {action.verb}")
            reg.register(action)
    return reg

