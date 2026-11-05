"""管理存储层单测：schema/版本/深合并/审计掩码/密钥加密与零明文。"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wsim.admin import audit, secrets, settings  # noqa: E402


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "service.sqlite"
    settings.init_schema(path)
    return path


@pytest.fixture
def master(monkeypatch: pytest.MonkeyPatch) -> str:
    key = base64.b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setenv(secrets.MASTER_KEY_ENV, key)
    return key


def test_schema_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "service.sqlite"
    settings.init_schema(path)
    settings.init_schema(path)
    conn = sqlite3.connect(str(path))
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'admin_%'"
            )
        }
    finally:
        conn.close()
    assert version == settings.SCHEMA_VERSION
    assert {
        "admin_settings",
        "admin_secrets",
        "admin_audit",
        "admin_sessions",
        "admin_login_failures",
        "admin_runtime",
        "admin_usage",
    } <= tables


def test_defaults_and_deep_merge(db_path: Path) -> None:
    store = settings.SettingsStore(db_path)
    default = store.get_all()
    assert default["world"]["ambient_speed"] == 1
    assert set(default["routing"]) == set(settings.DEFAULT_SETTINGS["routing"])
    assert store.version() == 0

    result = store.update(
        {
            "world": {"ambient_speed": 3},
            "llm": {"per_capability_daily_tokens": {"dialogue": 500}},
        },
        who="tester",
    )
    assert result["version"] == 1
    assert result["changed"] == ["ambient_speed", "per_capability_daily_tokens"]
    # 预算键已在真实调用点接线，属于 hot；世界参数仍需重启。
    assert result["requires_restart"] == ["ambient_speed"]

    merged = store.get_all()
    assert merged["world"]["ambient_speed"] == 3
    assert merged["world"]["ambient_cpu"] == 70
    assert merged["llm"]["per_capability_daily_tokens"]["dialogue"] == 500
    assert merged["llm"]["per_capability_daily_tokens"]["strategy"] == 0


def test_hot_keys_and_requires_restart() -> None:
    assert settings.HOT["global_enabled"] is True
    assert settings.HOT["providers"] is True
    assert settings.HOT["ambient_speed"] is False
    assert settings.requires_restart(["global_enabled"]) == []
    assert settings.requires_restart(["ambient_speed", "providers"]) == ["ambient_speed"]
    hot = settings.hot_map()
    assert hot["ambient_speed"] is False
    assert "重启" in settings.hot_notes()["ambient_speed"]


def test_update_rejects_unknown_keys(db_path: Path) -> None:
    store = settings.SettingsStore(db_path)
    with pytest.raises(settings.AdminValidationError):
        store.update({"nope": {"x": 1}})
    with pytest.raises(settings.AdminValidationError):
        store.update({"world": {"not_a_key": 1}})
    with pytest.raises(settings.AdminValidationError):
        store.update({"world": {"ambient_speed": -1}})
    with pytest.raises(settings.AdminValidationError):
        store.update({"llm": {"global_enabled": "yes"}})


def test_audit_records_and_mask(db_path: Path) -> None:
    store = settings.SettingsStore(db_path)
    store.update({"llm": {"global_enabled": False}}, who="ops")
    rows = audit.list_records(db_path, limit=10)
    assert rows and rows[0]["who"] == "ops"
    assert rows[0]["action"] == "settings.update"
    assert rows[0]["key"] == "llm"
    assert rows[0]["after"]["global_enabled"] is False

    # 直接写一条含敏感字段的审计，验证双层掩码。
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        audit.append(
            conn,
            who="ops",
            action="provider.put",
            key="relay-a",
            before={"api_key": "sk-abcdefghijklmnop"},
            after={"api_key": "sk-zzzzzzzzzzzzzzzz", "model": "m"},
            detail={"token": "super-secret-token-value"},
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    latest = audit.list_records(db_path, limit=1)[0]
    assert latest["after"]["api_key"] == "sk-z****zzzz"
    assert latest["before"]["api_key"] == "sk-a****mnop"
    assert latest["detail"]["token"] == "supe****alue"
    raw = json.dumps(latest, ensure_ascii=False)
    assert "sk-abcdefghijklmnop" not in raw
    assert "super-secret-token-value" not in raw


def test_secret_roundtrip_and_mask(db_path: Path, master: str) -> None:
    store = secrets.SecretStore(db_path)
    plain = "sk-" + "A1b2C3d4E5f6"
    store.put("relay-a", plain, who="ops")
    assert store.get("relay-a") == plain
    assert store.masked("relay-a") == "sk-A****E5f6"
    assert secrets.mask_secret(plain) == "sk-A****E5f6"
    assert secrets.mask_secret("short") == "*****"
    assert store.has("relay-a") is True
    store.delete("relay-a")
    assert store.has("relay-a") is False
    with pytest.raises(KeyError):
        store.get("relay-a")


def test_ciphertext_contains_no_plaintext(db_path: Path, master: str) -> None:
    plain = "sk-" + os.urandom(24).hex()
    secrets.SecretStore(db_path).put("relay-a", plain, who="ops")
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT ciphertext, nonce FROM admin_secrets").fetchall()
    finally:
        conn.close()
    assert rows and rows[0][0] is not None
    joined = b"".join(bytes(part) for row in rows for part in (row[0], row[1]))
    assert plain.encode("utf-8") not in joined
    # 明文也不得出现在任何文本表。
    raw = db_path.read_bytes()
    assert plain.encode("utf-8") not in raw


def test_master_key_missing(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(secrets.MASTER_KEY_ENV, raising=False)
    with pytest.raises(secrets.MasterKeyMissing) as exc:
        secrets.SecretStore(db_path).put("relay-a", "sk-whatever", who="ops")
    assert exc.value.code == "master_key_missing"
    assert secrets.has_master_key() is False

    monkeypatch.setenv(secrets.MASTER_KEY_ENV, base64.b64encode(b"short").decode("ascii"))
    with pytest.raises(secrets.MasterKeyMissing):
        secrets.master_key()


def test_no_plaintext_in_logs_and_audit(
    db_path: Path, master: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = "sk-" + os.urandom(24).hex()
    store = secrets.SecretStore(db_path)
    store.put("relay-a", plain, who="ops")
    settings.SettingsStore(db_path).update(
        {"providers": [{"id": "relay-a", "name": "A", "model": "m", "base_url": "http://x"}]},
        who="ops",
    )
    captured = capsys.readouterr()
    audit_text = json.dumps(audit.list_records(db_path, limit=100), ensure_ascii=False)
    assert plain not in captured.out
    assert plain not in captured.err
    assert plain not in audit_text
    # 审计原文（含历史行）也不得含明文。
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT before_json, after_json, detail_json FROM admin_audit"
        ).fetchall()
    finally:
        conn.close()
    assert plain not in "\n".join("".join(map(str, row)) for row in rows)


def test_settings_survive_reopen(db_path: Path) -> None:
    settings.SettingsStore(db_path).update(
        {"providers": [{"id": "relay-a", "base_url": "http://127.0.0.1:9", "model": "m"}],
         "routing": {"dialogue": {"provider": "relay-a", "fallback": []}}},
        who="ops",
    )
    reopened = settings.SettingsStore(db_path)
    assert reopened.version() == 1
    providers = reopened.providers()
    assert providers and providers[0]["id"] == "relay-a"
    assert reopened.routing()["dialogue"]["provider"] == "relay-a"


def test_api_key_never_persisted_in_settings(db_path: Path) -> None:
    settings.SettingsStore(db_path).update(
        {"providers": [{"id": "relay-a", "api_key": "sk-should-not-persist", "model": "m"}]},
        who="ops",
    )
    raw = db_path.read_bytes()
    assert b"sk-should-not-persist" not in raw


def test_init_migrates_v1_schema(tmp_path: Path) -> None:
    path = tmp_path / "service.sqlite"
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE admin_secrets (
                id TEXT PRIMARY KEY,
                ciphertext BLOB NOT NULL,
                nonce BLOB NOT NULL,
                updated_at REAL NOT NULL,
                updated_by TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.execute(
            "INSERT INTO admin_secrets(id, ciphertext, nonce, updated_at, updated_by)"
            " VALUES ('relay-a', x'00', x'00', 123.5, 'ops')"
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()

    settings.init_schema(path)
    settings.init_schema(path)  # 重复调用幂等

    conn = sqlite3.connect(str(path))
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(admin_secrets)")}
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        row = conn.execute(
            "SELECT created_at, last_used_at FROM admin_secrets WHERE id = 'relay-a'"
        ).fetchone()
        tables = {
            str(item[0])
            for item in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name IN ('admin_runtime', 'admin_usage')"
            )
        }
    finally:
        conn.close()
    assert {"created_at", "last_used_at"} <= columns
    assert version == settings.SCHEMA_VERSION
    assert row == (123.5, 0.0)
    assert tables == {"admin_runtime", "admin_usage"}
    settings.RuntimeStore(path).set("world_state", {"ambient_speed": {"value": 1}})


def test_runtime_store_does_not_touch_settings_version(db_path: Path) -> None:
    runtime = settings.RuntimeStore(db_path, now_fn=lambda: 1000.0)
    runtime.set("world_state", {"ambient_speed": {"value": 2}})
    assert runtime.get("world_state")["ambient_speed"]["value"] == 2
    assert runtime.get("missing", {"fallback": True}) == {"fallback": True}
    assert set(runtime.all()) == {"world_state"}
    runtime.delete("world_state")
    assert runtime.get("world_state") is None

    # 运行态不得把配置版本号推高。
    assert settings.SettingsStore(db_path).version() == 0
    assert settings.default_world_state()["ambient_speed"]["effective_value"] is None


def test_secret_meta_and_touch(
    db_path: Path, master: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = secrets.SecretStore(db_path)
    plain = "sk-abcdefghijklmnop"
    store.put("relay-a", plain, who="ops")
    meta = store.meta("relay-a")
    assert meta["has"] is True
    assert meta["masked"] == "sk-a****mnop"
    assert meta["length"] == len(plain)
    assert meta["created_at"] is not None and meta["created_at"].endswith("Z")
    assert meta["updated_at"] is not None and meta["updated_at"].endswith("Z")
    assert meta["last_used_at"] is None  # 0 视为未使用
    assert plain not in json.dumps(meta, ensure_ascii=False)

    store.touch("relay-a", at=1700000000.0)
    assert store.meta("relay-a")["last_used_at"] == "2023-11-14T22:13:20Z"

    # 改 key 保留 created_at / last_used_at。
    created = store.meta("relay-a")["created_at"]
    store.put("relay-a", "sk-second-key-12345", who="ops")
    after = store.meta("relay-a")
    assert after["created_at"] == created
    assert after["last_used_at"] == "2023-11-14T22:13:20Z"
    assert after["masked"] == "sk-s****2345"

    # 密文损坏：has 仍为真，但掩码/长度退化为空，不冒泡。
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE admin_secrets SET ciphertext = x'00' WHERE id = 'relay-a'")
        conn.commit()
    finally:
        conn.close()
    broken = store.meta("relay-a")
    assert broken["has"] is True
    assert broken["masked"] == ""
    assert broken["length"] == 0

    store.touch("missing-provider", at=1.0)  # 无行：静默忽略

    monkeypatch.delenv(secrets.MASTER_KEY_ENV, raising=False)
    with pytest.raises(secrets.MasterKeyMissing):
        store.meta("relay-a")
