"""Action 插件加载器。

契约（P2）：
- 每个插件一个目录，至少包含 `manifest.yaml` 和 manifest.entry 指向的 Python 文件。
- manifest 声明 id / verb / version / args schema / preconditions / effects。
- Python 入口只暴露纯函数 `apply(state, args) -> list[Effect]`。
- 插件只能返回 Effect，不能直接改 state；适配器会在调用前后比较 state 哈希，
  发现变更即判为违约并让 Writer 拒绝整笔事务。
- 返回的 Effect op 必须在 manifest.effects、全局 EFFECT_OPS 中，且不能是 mint。

内核保留能力（插件不可绕过）：单写者、事务/回滚、事件日志、Effect 原语语义、
PRNG/虚拟时钟、MCP 权限与审计。详见 ARCHITECTURE.md。
"""
from __future__ import annotations

import copy
import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

from ..core.models import Effect, EffectError, Intent, Reject
from ..world.effects import EFFECT_OPS, PLUGIN_FORBIDDEN_OPS
from ..world.state import WorldState

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


class PluginError(EffectError):
    """插件 manifest 或 apply 违约；继承 EffectError，Writer 会整笔拒绝。"""


@dataclass
class PluginManifest:
    id: str
    verb: str
    version: str
    entry: str
    args: dict[str, Any] = field(default_factory=dict)
    preconditions: list[dict[str, Any]] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    path: Path | None = None
    module: Any = None


def _fill(template: str, args: dict[str, Any]) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in args:
            raise PluginError(f"missing_arg:{key}")
        return str(args[key])

    return re.sub(r"\{(\w+)\}", repl, template)


def _entity(state: WorldState, eid: str) -> Any:
    ent = state.entities.get(eid)
    if ent is None:
        raise Reject("unknown_entity")
    if ent.status != "alive":
        raise Reject("entity_not_alive")
    return ent


def _check_condition(state: WorldState, cond: dict[str, Any], actor: str, args: dict[str, Any]) -> list[str]:
    kind = cond.get("kind")
    if kind in ("entity_exists", "entity_alive"):
        name = cond.get("arg", "actor")
        value = actor if name == "actor" else args.get(name)
        if value is None:
            return [f"missing_arg:{name}"]
        ent = state.entities.get(str(value))
        if ent is None:
            return [f"unknown_entity:{value}"]
        if kind == "entity_alive" and ent.status != "alive":
            return [f"entity_not_alive:{value}"]
        return []
    if kind == "not_self":
        name = cond.get("arg", "target")
        value = args.get(name)
        if value == actor:
            return ["target_is_self"]
        return []
    if kind == "positive_int":
        name = cond.get("arg")
        value = args.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return [f"bad_arg:{name}"]
        return []
    if kind == "balance_gte":
        account = _fill(str(cond.get("account", "")), args)
        name = cond.get("arg", "amount")
        amount = args.get(name)
        if isinstance(amount, bool) or not isinstance(amount, int):
            return [f"bad_arg:{name}"]
        if state.account_balance(account) < amount:
            return [f"insufficient_balance:{account}"]
        return []
    if kind == "same_location":
        a = actor
        b = args.get(cond.get("arg", "target"))
        ea, eb = state.entities.get(a), state.entities.get(str(b))
        if ea is None or eb is None:
            return ["unknown_entity"]
        if ea.location != eb.location:
            return ["wrong_location"]
        return []
    return [f"unknown_precondition:{kind}"]


class PluginAction:
    """把插件纯函数适配成 Action；所有 Effect 都经 Writer 的 clone/事务路径。"""

    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest
        self.verb = manifest.verb

    def preconditions(self, state: WorldState, intent: Intent) -> list[str]:
        reasons: list[str] = []
        for cond in self.manifest.preconditions:
            try:
                reasons.extend(_check_condition(state, cond, intent.actor, intent.args))
            except Reject as exc:
                reasons.append(exc.reason)
            except PluginError as exc:
                reasons.append(str(exc))
        return reasons

    def apply(self, state: WorldState, intent: Intent, rng) -> list[Effect]:
        # 插件拿到的是 state 的深拷贝：即使插件试图写入，也碰不到真实状态。
        # 调用后再比较拷贝的哈希，发现变更即违约，整笔事务由 Writer 拒绝。
        sandbox = copy.deepcopy(state)
        before = sandbox.hash()
        call_args = {"actor": intent.actor}
        call_args.update(dict(intent.args))
        try:
            raw = self.manifest.module.apply(sandbox, call_args)
        except (Reject, PluginError):
            raise
        except Exception as exc:  # 插件内部错误统一变成可读的 EffectError
            raise PluginError(f"plugin_apply_error:{type(exc).__name__}:{exc}") from exc
        if sandbox.hash() != before:
            raise PluginError("plugin_mutated_state")
        if not isinstance(raw, list):
            raise PluginError("plugin_must_return_effect_list")
        effects: list[Effect] = []
        for item in raw:
            if not isinstance(item, Effect):
                raise PluginError("plugin_effect_must_be_Effect")
            if item.op not in EFFECT_OPS:
                raise PluginError(f"unknown_effect:{item.op}")
            if item.op in PLUGIN_FORBIDDEN_OPS:
                raise PluginError(f"forbidden_effect:{item.op}")
            if item.op not in self.manifest.effects:
                raise PluginError(f"undeclared_effect:{item.op}")
            effects.append(Effect(item.op, copy.deepcopy(item.data)))
        return effects


def _load_manifest(path: Path, known_effects: frozenset[str], forbidden: frozenset[str]) -> PluginManifest:
    if yaml is None:
        raise PluginError(f"{path}: PyYAML 未安装")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PluginError(f"{path}: YAML 解析失败: {exc}") from exc
    if not isinstance(raw, dict):
        raise PluginError(f"{path}: 顶层必须是映射")
    errors: list[str] = []
    for key in ("id", "verb", "version", "entry", "effects", "preconditions"):
        if key not in raw or raw.get(key) in (None, ""):
            errors.append(f"缺少字段 {key}")
    if errors:
        raise PluginError(f"{path}: " + "; ".join(errors))
    if not _VERSION_RE.match(str(raw["version"])):
        raise PluginError(f"{path}: version 必须是 x.y.z")
    if not isinstance(raw.get("args"), dict):
        raise PluginError(f"{path}: args 必须是 {name: type} 映射")
    if not isinstance(raw.get("preconditions"), list):
        raise PluginError(f"{path}: preconditions 必须是列表")
    effects = raw.get("effects")
    if not isinstance(effects, list) or not all(isinstance(x, str) for x in effects):
        raise PluginError(f"{path}: effects 必须是字符串列表")
    unknown = [op for op in effects if op not in known_effects]
    if unknown:
        raise PluginError(f"{path}: 未知 Effect 原语: {', '.join(unknown)}")
    forbidden_ops = [op for op in effects if op in forbidden]
    if forbidden_ops:
        raise PluginError(f"{path}: 插件禁止使用原语: {', '.join(forbidden_ops)}")
    return PluginManifest(
        id=str(raw["id"]),
        verb=str(raw["verb"]),
        version=str(raw["version"]),
        entry=str(raw["entry"]),
        args=dict(raw.get("args") or {}),
        preconditions=list(raw.get("preconditions") or []),
        effects=list(effects),
        path=path,
    )


def _load_module(entry: Path, plugin_id: str) -> Any:
    if not entry.exists():
        raise PluginError(f"插件入口不存在: {entry}")
    name = "wsim_plugin_" + re.sub(r"[^A-Za-z0-9_]", "_", plugin_id)
    spec = importlib.util.spec_from_file_location(name, entry)
    if spec is None or spec.loader is None:
        raise PluginError(f"无法加载插件模块: {entry}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "apply", None)):
        raise PluginError(f"{entry}: 必须导出 apply(state, args)")
    return module


def load_plugins(
    plugin_dir: Path,
    *,
    known_effects: frozenset[str] = EFFECT_OPS,
    forbidden: frozenset[str] = PLUGIN_FORBIDDEN_OPS,
) -> list[PluginAction]:
    if not plugin_dir.is_dir():
        return []
    out: list[PluginAction] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    for sub in sorted(p for p in plugin_dir.iterdir() if p.is_dir()):
        mpath = sub / "manifest.yaml"
        if not mpath.exists():
            errors.append(f"{sub}: 缺少 manifest.yaml")
            continue
        try:
            manifest = _load_manifest(mpath, known_effects, forbidden)
        except PluginError as exc:
            errors.append(str(exc))
            continue
        if manifest.id in seen_ids:
            errors.append(f"{mpath}: 插件 id 重复: {manifest.id}")
            continue
        seen_ids.add(manifest.id)
        try:
            manifest.module = _load_module(sub / manifest.entry, manifest.id)
        except PluginError as exc:
            errors.append(str(exc))
            continue
        out.append(PluginAction(manifest))
    if errors:
        raise PluginError("插件加载失败:\n" + "\n".join(errors))
    return out
