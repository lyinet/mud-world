"""管理配置存储：`service.sqlite` 里的 admin_settings / admin_secrets / admin_audit。

- 幂等建表：`PRAGMA user_version` 记录 schema 版本。
- 顶层段落按行存放：world / llm / providers / routing。
- 全局版本号 = `MAX(admin_settings.version)`；每次写入把受影响行版本设为旧全局版本 + 1。
- 写配置与写审计在同一 `BEGIN IMMEDIATE` 事务内完成。
- 行里的 `providers[*]` 永不包含 `api_key`（key 只进 admin_secrets）。
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterable

from . import audit
from .clock import now_utc

SCHEMA_VERSION = 3
SECTIONS = ("world", "llm", "providers", "routing")
OBSERVED_WORLD_KEYS = ("ambient_speed", "ambient_cpu")

DEFAULT_SETTINGS: dict[str, Any] = {
    "world": {
        "tick_minutes": 10,
        "ambient_speed": 1,
        "ambient_cpu": 70,
        "player_cap": 200,
        "starting_money": 300,
        "quest_floor_enabled": True,
    },
    "llm": {
        "global_enabled": True,
        "daily_budget_usd": 0.20,
        "per_capability_daily_tokens": {"dialogue": 0, "strategy": 0},
    },
    "providers": [],
    "routing": {
        capability: {"provider": "", "fallback": []}
        for capability in (
            "dialogue",
            "strategy",
            "self_goal",
            "night_batch",
            "quest_gen",
            "persona_gen",
            "validation",
        )
    },
}

WORLD_KEYS = tuple(DEFAULT_SETTINGS["world"])

# hot=True 表示主服务无需重启即可生效；False 必须重启（或尚未接线）。
HOT: dict[str, bool] = {
    "ambient_speed": False,
    "ambient_cpu": False,
    "tick_minutes": False,
    "player_cap": False,
    "starting_money": False,
    "quest_floor_enabled": False,
    "global_enabled": True,
    "daily_budget_usd": True,
    "per_capability_daily_tokens": True,
    "providers": True,
    "routing": True,
}

HOT_NOTES: dict[str, str] = {
    "ambient_speed": "只在启动时读 XKX_AMBIENT_SPEED，需重启玩家 API",
    "ambient_cpu": "只在启动时读 XKX_AMBIENT_CPU，需重启玩家 API",
    "tick_minutes": "主服务当前无运行期读取点",
    "player_cap": "主服务当前无运行期读取点",
    "starting_money": "主服务当前无运行期读取点",
    "quest_floor_enabled": "主服务当前无运行期读取点",
    "global_enabled": "provider 快照 ≤5s 生效（killswitch）",
    "daily_budget_usd": "真实调用前按自然日（UTC）累计拦截；≤0 表示不限",
    "per_capability_daily_tokens": "按能力位自然日 token 上限（输入+输出）；≤0 表示不限",
    "providers": "provider 快照 ≤5s 生效",
    "routing": "provider 快照 ≤5s 生效",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    updated_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS admin_secrets (
    id TEXT PRIMARY KEY,
    ciphertext BLOB NOT NULL,
    nonce BLOB NOT NULL,
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    last_used_at REAL NOT NULL DEFAULT 0,
    updated_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS admin_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    who TEXT NOT NULL,
    action TEXT NOT NULL,
    key TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT '',
    after_json TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    who TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_login_failures (
    ip TEXT NOT NULL,
    failed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_login_failures ON admin_login_failures(ip, failed_at);
CREATE TABLE IF NOT EXISTS admin_runtime (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_usage (
    day TEXT NOT NULL,
    capability TEXT NOT NULL,
    provider_id TEXT NOT NULL DEFAULT '',
    calls INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    denied INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (day, capability, provider_id)
);
"""


class AdminValidationError(ValueError):
    code = "admin_bad_request"


def default_settings() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_SETTINGS)


def resolve_db_path(
    root: str | Path | None = None,
    run: str | None = None,
    explicit: str | Path | None = None,
) -> Path:
    """优先级：显式参数 > XKX_ADMIN_DB > XKX_SERVICE_DB > <root>/runs/<run>/service.sqlite。"""
    if explicit:
        return Path(explicit)
    for env_key in ("XKX_ADMIN_DB", "XKX_SERVICE_DB"):
        value = str(os.environ.get(env_key, "")).strip()
        if value:
            return Path(value)
    base = Path(root or ".").resolve()
    run_id = str(run or os.environ.get("XKX_RUN") or "player-demo").strip() or "player-demo"
    return base / "runs" / run_id / "service.sqlite"


def connect(path: str | Path, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if readonly:
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    else:
        conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(path: str | Path) -> None:
    """幂等建表/迁移；重复调用不破坏已有数据、不重复 ALTER。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        # 始终执行建表脚本：老库（user_version >= 版本）也可能缺 admin_runtime。
        conn.executescript(_SCHEMA)
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(admin_secrets)").fetchall()
        }
        if "created_at" not in columns:
            conn.execute(
                "ALTER TABLE admin_secrets ADD COLUMN created_at REAL NOT NULL DEFAULT 0"
            )
        if "last_used_at" not in columns:
            conn.execute(
                "ALTER TABLE admin_secrets ADD COLUMN last_used_at REAL NOT NULL DEFAULT 0"
            )
        # v1 老行没有 created_at：用 updated_at 回填；已是非 0 的行不重复写。
        conn.execute("UPDATE admin_secrets SET created_at = updated_at WHERE created_at = 0")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def default_world_state() -> dict[str, dict[str, Any]]:
    """每个 world 键的运行观测契约（D1/D5）；默认全部“待重启生效”。"""
    out: dict[str, dict[str, Any]] = {}
    for key, value in DEFAULT_SETTINGS["world"].items():
        out[key] = {
            "value": copy.deepcopy(value),
            "effective_value": None,
            "applied": False,
            "restart_required": True,
            "source": "none",
            "observed_at": None,
            "note": HOT_NOTES.get(key, ""),
        }
    return out


class RuntimeStore:
    """`admin_runtime` 读写：运行态与配置分表，绝不改 `admin_settings.version`。"""

    def __init__(self, path: str | Path, now_fn: Callable[[], float] | None = None):
        self.path = Path(path)
        self.now = now_fn or now_utc

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, key: str, default: Any = None) -> Any:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT value_json FROM admin_runtime WHERE key = ?", (str(key),)
            ).fetchone()
        except sqlite3.Error:
            return default
        finally:
            conn.close()
        if row is None:
            return default
        try:
            return json.loads(str(row[0]))
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value: Any, now: float | None = None) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO admin_runtime(key, value_json, updated_at) VALUES (?, ?, ?)",
                (
                    str(key),
                    json.dumps(value, sort_keys=True, ensure_ascii=False),
                    float(self.now() if now is None else now),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def delete(self, key: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM admin_runtime WHERE key = ?", (str(key),))
            conn.commit()
        finally:
            conn.close()

    def all(self) -> dict[str, Any]:
        conn = self._conn()
        try:
            rows = conn.execute("SELECT key, value_json FROM admin_runtime").fetchall()
        except sqlite3.Error:
            return {}
        finally:
            conn.close()
        out: dict[str, Any] = {}
        for row in rows:
            try:
                out[str(row[0])] = json.loads(str(row[1]))
            except (TypeError, ValueError):
                continue
        return out


class UsageStore:
    """`admin_usage` 读写：按 自然日 + 能力位 + provider 累加用量与拒绝次数。

    - 只增不改其它表；不影响 `admin_settings.version`。
    - 库不存在时读返回空、写直接跳过（没有管理配置就没有预算闸）。
    """

    def __init__(self, path: str | Path, now_fn: Callable[[], float] | None = None):
        self.path = Path(path)
        self.now = now_fn or now_utc

    def rows(self, day: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        uri = f"file:{self.path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        try:
            result = conn.execute(
                "SELECT day, capability, provider_id, calls, tokens_in, tokens_out,"
                " cost_usd, denied, updated_at FROM admin_usage WHERE day = ?",
                (str(day),),
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "day": str(row[0]),
                "capability": str(row[1]),
                "provider_id": str(row[2]),
                "calls": int(row[3] or 0),
                "tokens_in": int(row[4] or 0),
                "tokens_out": int(row[5] or 0),
                "cost_usd": float(row[6] or 0.0),
                "denied": int(row[7] or 0),
                "updated_at": float(row[8] or 0.0),
            }
            for row in result
        ]

    def add(
        self,
        day: str,
        capability: str,
        provider_id: str,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        calls: int = 1,
        now: float | None = None,
    ) -> None:
        if not self.path.exists():
            return
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        try:
            conn.execute(
                "INSERT INTO admin_usage"
                "(day, capability, provider_id, calls, tokens_in, tokens_out, cost_usd,"
                " denied, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)"
                " ON CONFLICT(day, capability, provider_id) DO UPDATE SET"
                " calls = admin_usage.calls + excluded.calls,"
                " tokens_in = admin_usage.tokens_in + excluded.tokens_in,"
                " tokens_out = admin_usage.tokens_out + excluded.tokens_out,"
                " cost_usd = admin_usage.cost_usd + excluded.cost_usd,"
                " updated_at = excluded.updated_at",
                (
                    str(day),
                    str(capability),
                    str(provider_id),
                    int(calls),
                    int(tokens_in),
                    int(tokens_out),
                    float(cost_usd),
                    float(self.now() if now is None else now),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def add_denial(
        self,
        day: str,
        capability: str,
        provider_id: str,
        *,
        now: float | None = None,
    ) -> int:
        """记一次拒绝，返回该 (day, capability, provider) 之前的拒绝次数。"""
        if not self.path.exists():
            return 0
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT denied FROM admin_usage WHERE day = ? AND capability = ? AND provider_id = ?",
                (str(day), str(capability), str(provider_id)),
            ).fetchone()
            previous = int(row[0] or 0) if row else 0
            conn.execute(
                "INSERT INTO admin_usage"
                "(day, capability, provider_id, calls, tokens_in, tokens_out, cost_usd,"
                " denied, updated_at) VALUES (?, ?, ?, 0, 0, 0, 0, 1, ?)"
                " ON CONFLICT(day, capability, provider_id) DO UPDATE SET"
                " denied = admin_usage.denied + 1, updated_at = excluded.updated_at",
                (str(day), str(capability), str(provider_id), float(self.now() if now is None else now)),
            )
            conn.execute("COMMIT")
            return previous
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()


def deep_merge(base: Any, patch: Any) -> Any:
    """dict 递归合并；list 整体替换；标量覆盖。"""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = copy.deepcopy(base)
        for key, value in patch.items():
            if key in out:
                out[key] = deep_merge(out[key], value)
            else:
                out[key] = copy.deepcopy(value)
        return out
    return copy.deepcopy(patch)


def strip_secret_fields(value: Any) -> Any:
    """持久化前剔除 api_key 等敏感字段（key 只进 admin_secrets）。"""
    if isinstance(value, dict):
        return {
            str(k): strip_secret_fields(v)
            for k, v in value.items()
            if not audit.is_sensitive_key(str(k))
        }
    if isinstance(value, list):
        return [strip_secret_fields(item) for item in value]
    return value


def requires_restart(fields: Iterable[str]) -> list[str]:
    return sorted({str(f) for f in fields if not HOT.get(str(f), False)})


def hot_map() -> dict[str, bool]:
    """扁平布尔表，供前端直观展示；说明见 `hot_notes()`。"""
    return {key: bool(value) for key, value in HOT.items()}


def hot_notes() -> dict[str, str]:
    return {key: HOT_NOTES.get(key, "") for key in HOT}


class SettingsStore:
    """配置段落的读写；每次操作打开短连接，避免跨进程长期持锁。"""

    def __init__(self, path: str | Path, now_fn: Callable[[], float] | None = None):
        self.path = Path(path)
        self.now = now_fn or now_utc
        init_schema(self.path)

    # ---------------------------------------------------------------- 读
    def version(self) -> int:
        conn = self._conn()
        try:
            row = conn.execute("SELECT MAX(version) FROM admin_settings").fetchone()
        finally:
            conn.close()
        return int(row[0] or 0) if row else 0

    def _stored_sections(self) -> dict[str, Any]:
        conn = self._conn()
        try:
            rows = conn.execute("SELECT key, value_json FROM admin_settings").fetchall()
        finally:
            conn.close()
        raw: dict[str, Any] = {}
        for row in rows:
            try:
                raw[str(row[0])] = json.loads(str(row[1]))
            except (TypeError, ValueError):
                continue
        return raw

    def get_all(self) -> dict[str, Any]:
        merged = default_settings()
        for section, value in self._stored_sections().items():
            if section in SECTIONS:
                merged[section] = deep_merge(merged[section], value)
        return merged

    def get_section(self, section: str) -> Any:
        if section not in SECTIONS:
            raise AdminValidationError(f"unknown_section:{section}")
        return self.get_all()[section]

    def providers(self) -> list[dict[str, Any]]:
        value = self.get_section("providers")
        return list(value) if isinstance(value, list) else []

    def routing(self) -> dict[str, Any]:
        value = self.get_section("routing")
        return dict(value) if isinstance(value, dict) else {}

    def global_enabled(self) -> bool:
        return bool(self.get_section("llm").get("global_enabled", True))

    # ---------------------------------------------------------------- 写
    def update(
        self,
        patch: dict[str, Any],
        who: str = "admin",
        action: str = "settings.update",
        key: str = "",
    ) -> dict[str, Any]:
        """深合并 patch 并落库；返回 {version, requires_restart, changed}。"""
        if not isinstance(patch, dict) or not patch:
            raise AdminValidationError("empty_patch")
        unknown = sorted(set(patch) - set(SECTIONS))
        if unknown:
            raise AdminValidationError("unknown_section:" + ",".join(unknown))
        self._validate_patch(patch)

        changed_fields: list[str] = []
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            before_all = self._read_all_in(conn)
            current_version = int(
                conn.execute("SELECT MAX(version) FROM admin_settings").fetchone()[0] or 0
            )
            new_version = current_version + 1
            after_all = copy.deepcopy(before_all)
            written: dict[str, Any] = {}
            for section in SECTIONS:
                if section not in patch:
                    continue
                merged = strip_secret_fields(
                    deep_merge(before_all.get(section, DEFAULT_SETTINGS[section]), patch[section])
                )
                if merged == before_all.get(section):
                    continue
                written[section] = merged
                after_all[section] = merged
                conn.execute(
                    "INSERT INTO admin_settings(key, value_json, version, updated_at, updated_by)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,"
                    " version = excluded.version, updated_at = excluded.updated_at,"
                    " updated_by = excluded.updated_by",
                    (
                        section,
                        json.dumps(merged, ensure_ascii=False, sort_keys=True),
                        new_version,
                        float(self.now()),
                        str(who),
                    ),
                )
                changed_fields.extend(_change_fields(section, before_all.get(section), merged))
            if not written:
                conn.execute("COMMIT")
                return {
                    "version": current_version,
                    "requires_restart": [],
                    "changed": [],
                    "noop": True,
                }
            for section in written:
                audit.append(
                    conn,
                    who=who,
                    action=action,
                    key=key or section,
                    before=before_all.get(section, {}),
                    after=after_all.get(section, {}),
                    detail={"section": section},
                    at=float(self.now()),
                )
            conn.execute("COMMIT")
            return {
                "version": new_version,
                "requires_restart": requires_restart(changed_fields),
                "changed": sorted(set(changed_fields)),
                "noop": False,
            }
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def bump_version(
        self,
        section: str = "providers",
        *,
        who: str = "admin",
        action: str = "provider.key_rotate",
        key: str = "",
        detail: dict[str, Any] | None = None,
    ) -> int:
        """只递增版本号并写审计，不改配置值。

        用于 key 轮换：密文在 `admin_secrets`，不进 `admin_settings`，但主服务
        必须感知到快照失效。审计里只放掩码。
        """
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = int(
                conn.execute("SELECT MAX(version) FROM admin_settings").fetchone()[0] or 0
            )
            new_version = current + 1
            conn.execute(
                "UPDATE admin_settings SET version = ?, updated_at = ?, updated_by = ?"
                " WHERE key = ?",
                (new_version, float(self.now()), str(who), str(section)),
            )
            audit.append(
                conn,
                who=who,
                action=action,
                key=key or section,
                before=None,
                after=None,
                detail=detail or {},
                at=float(self.now()),
            )
            conn.execute("COMMIT")
            return new_version
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _read_all_in(conn: sqlite3.Connection) -> dict[str, Any]:
        merged = default_settings()
        rows = conn.execute("SELECT key, value_json FROM admin_settings").fetchall()
        for row in rows:
            section = str(row[0])
            if section not in SECTIONS:
                continue
            try:
                value = json.loads(str(row[1]))
            except (TypeError, ValueError):
                continue
            merged[section] = deep_merge(merged[section], value)
        return merged

    @staticmethod
    def _validate_patch(patch: dict[str, Any]) -> None:
        if "world" in patch:
            world = patch["world"]
            if not isinstance(world, dict):
                raise AdminValidationError("world_not_object")
            for name, value in world.items():
                if name not in DEFAULT_SETTINGS["world"]:
                    raise AdminValidationError(f"unknown_world_key:{name}")
                if name == "quest_floor_enabled":
                    if not isinstance(value, bool):
                        raise AdminValidationError("quest_floor_enabled_not_bool")
                elif not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise AdminValidationError(f"{name}_not_number")
                elif float(value) < 0:
                    raise AdminValidationError(f"{name}_negative")
        if "llm" in patch:
            llm = patch["llm"]
            if not isinstance(llm, dict):
                raise AdminValidationError("llm_not_object")
            allowed = set(DEFAULT_SETTINGS["llm"])
            unknown = sorted(set(llm) - allowed)
            if unknown:
                raise AdminValidationError("unknown_llm_key:" + ",".join(unknown))
            if "global_enabled" in llm and not isinstance(llm["global_enabled"], bool):
                raise AdminValidationError("global_enabled_not_bool")
            budget = llm.get("daily_budget_usd")
            if budget is not None:
                if isinstance(budget, bool) or not isinstance(budget, (int, float)):
                    raise AdminValidationError("daily_budget_usd_not_number")
                if float(budget) < 0:
                    raise AdminValidationError("daily_budget_usd_negative")
            tokens = llm.get("per_capability_daily_tokens")
            if tokens is not None:
                if not isinstance(tokens, dict):
                    raise AdminValidationError("per_capability_daily_tokens_not_object")
                for capability, value in tokens.items():
                    if capability not in DEFAULT_SETTINGS["routing"]:
                        raise AdminValidationError(f"unknown_capability:{capability}")
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        raise AdminValidationError(
                            f"per_capability_daily_tokens_invalid:{capability}"
                        )
        if "providers" in patch and not isinstance(patch["providers"], list):
            raise AdminValidationError("providers_not_list")
        if "routing" in patch:
            routing = patch["routing"]
            if not isinstance(routing, dict):
                raise AdminValidationError("routing_not_object")
            for capability, route in routing.items():
                if capability not in DEFAULT_SETTINGS["routing"]:
                    raise AdminValidationError(f"unknown_capability:{capability}")
                if not isinstance(route, dict):
                    raise AdminValidationError(f"route_not_object:{capability}")
                fallback = route.get("fallback", [])
                if not isinstance(fallback, list):
                    raise AdminValidationError(f"route_fallback_not_list:{capability}")


def _change_fields(section: str, before: Any, after: Any) -> list[str]:
    if section in ("providers", "routing"):
        return [section]
    out: list[str] = []
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            out.append(str(key))
    return out
