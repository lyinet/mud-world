# xkx 复刻版确定性审计

- commit: `e3232446859947b47c4ace58c37adb3b5db24629`
- 配置: days=2 seed=42 npc_limit=0 quick=False
- 汇总: 通过 6 / 不通过 1 / 未就绪 0 / 跳过 0
- 退出码: 1

| # | 审计项 | 状态 | 结论 |
|---|---|---|---|
| 1 | 同 seed 两次逐字节一致 | **PASS** | 两次 run 的 state/event 终态哈希与 events.jsonl sha256 完全一致 |
| 2 | 开关无关（模型配音 / 旁路文件） | **PASS** | 配音开/关、旁路文件有/无 两种路径世界哈希一致 |
| 3 | 事件全量重放等价 | **PASS** | 791 条事件全量重放后 state hash 与在线终态一致 |
| 4 | 无隐藏随机 | **PASS** | src/** 投影/效果路径未发现 random/time/uuid/os.urandom（另有 41 条非关键墙钟仅记录） |
| 5 | 账本无模型文本 | **PASS** | 模型台词只在旁路；事件库与状态均只含确定性模板句 |
| 6 | 旧世界 golden 基线未被静默改动 | **FAIL** | 不可追溯/未复现：未提交 0 组、理由缺失 1 条、新鲜 run 漂移 无、dialogue 基线漂移 无 |
| 7 | 战斗新红线 | **PASS** | 1 场真实 fight（默认世界内存夹具）自洽：5 回合、事件效果/状态/log 一致、同 seed 重打一致 |

## 结论

**红线失败（阻断）：**
- `golden_baseline_untouched`：不可追溯/未复现：未提交 0 组、理由缺失 1 条、新鲜 run 漂移 无、dialogue 基线漂移 无


## 证据

### same_seed_twice · 同 seed 两次逐字节一致 — PASS

两次 run 的 state/event 终态哈希与 events.jsonl sha256 完全一致

```json
{
  "compared": {
    "content_hash": {
      "a": "5267a8a84f7c2e0200358c92b8e63cebff54db461de8123484732a66aa696da4",
      "b": "5267a8a84f7c2e0200358c92b8e63cebff54db461de8123484732a66aa696da4",
      "same": true
    },
    "event_final": {
      "a": "0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49",
      "b": "0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49",
      "same": true
    },
    "events_total": {
      "a": 791,
      "b": 791,
      "same": true
    },
    "state_final": {
      "a": "6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55",
      "b": "6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55",
      "same": true
    }
  },
  "drift": {},
  "events_total": 791,
  "file_sha256": {
    "daily_jsonl": {
      "a": "1051bc9a7c6b7ec9c45317f508a99f88d40b5f9ec915f33916cb366357104bcf",
      "b": "1051bc9a7c6b7ec9c45317f508a99f88d40b5f9ec915f33916cb366357104bcf",
      "same": true
    },
    "event_hash": {
      "a": "7b86a5cfe555127f40e98b368bda14ff88235338a058302f94a5c702d0cf4ec8",
      "b": "7b86a5cfe555127f40e98b368bda14ff88235338a058302f94a5c702d0cf4ec8",
      "same": true
    },
    "events_jsonl": {
      "a": "670e09592afdafb8b5bcc4d7976b2372ff4219124520982f88261df08cc9b19d",
      "b": "670e09592afdafb8b5bcc4d7976b2372ff4219124520982f88261df08cc9b19d",
      "same": true
    },
    "events_sqlite": {
      "a": "37e1bca66a0b60f0d387fd057e4c8970ae40adeff6103a2446cb7d8b54bf8dad",
      "b": "37e1bca66a0b60f0d387fd057e4c8970ae40adeff6103a2446cb7d8b54bf8dad",
      "same": true
    },
    "world_hash": {
      "a": "f624c36342b6a4f99369a01b2b95b86b6591ff5763869c3207f008992198eeee",
      "b": "f624c36342b6a4f99369a01b2b95b86b6591ff5763869c3207f008992198eeee",
      "same": true
    }
  },
  "runs": [
    "/tmp/xkx-audit-final/same-a",
    "/tmp/xkx-audit-final/same-b"
  ]
}
```

### switch_independence · 开关无关（模型配音 / 旁路文件） — PASS

配音开/关、旁路文件有/无 两种路径世界哈希一致

```json
{
  "aside_deleted_hash_equal": true,
  "aside_deleted_reopen": {
    "event_hash": "75c443995cd502f0127001070f24e4a42b89e6d107f0b8845646b4fccfaf3474",
    "events_total": 8,
    "state_hash": "91593f2051c75c1b851c08102a6c9d160d3125c3a36789635500a26a59b51a32"
  },
  "hashes_switch_equal": true,
  "headless_dialogue_switch": {
    "dialogue_off": {
      "event_final": "0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49",
      "events_total": 791,
      "state_final": "6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55"
    },
    "dialogue_on": {
      "event_final": "0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49",
      "events_total": 791,
      "state_final": "6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55"
    },
    "hashes_equal": true,
    "ran": true
  },
  "model_off": {
    "event_hash": "75c443995cd502f0127001070f24e4a42b89e6d107f0b8845646b4fccfaf3474",
    "events_total": 8,
    "state_hash": "91593f2051c75c1b851c08102a6c9d160d3125c3a36789635500a26a59b51a32"
  },
  "model_on": {
    "aside_exists": true,
    "dialogue_calls": 1,
    "event_hash": "75c443995cd502f0127001070f24e4a42b89e6d107f0b8845646b4fccfaf3474",
    "events_total": 8,
    "state_hash": "91593f2051c75c1b851c08102a6c9d160d3125c3a36789635500a26a59b51a32"
  }
}
```

### replay_equivalence · 事件全量重放等价 — PASS

791 条事件全量重放后 state hash 与在线终态一致

```json
{
  "content_hash_run": "5267a8a84f7c2e0200358c92b8e63cebff54db461de8123484732a66aa696da4",
  "event_hash_replayed": "0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49",
  "event_hash_stored": "0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49",
  "events_replayed": 791,
  "mismatch": {},
  "state_replayed": "6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55",
  "state_stored": "6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55"
}
```

### no_hidden_random · 无隐藏随机 — PASS

src/** 投影/效果路径未发现 random/time/uuid/os.urandom（另有 41 条非关键墙钟仅记录）

```json
{
  "all_findings": [
    {
      "file": "src/wsim/admin/providers.py",
      "line": 677,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "started = time.perf_counter()"
    },
    {
      "file": "src/wsim/admin/providers.py",
      "line": 681,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "result[\"latency_ms\"] = round((time.perf_counter() - started) * 1000, 1)"
    },
    {
      "file": "src/wsim/admin/providers.py",
      "line": 684,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "result[\"latency_ms\"] = round((time.perf_counter() - started) * 1000, 1)"
    },
    {
      "file": "src/wsim/admin/secrets.py",
      "line": 69,
      "pattern": "os.urandom",
      "severity": "review",
      "snippet": "nonce = os.urandom(12)"
    },
    {
      "file": "src/wsim/admin/server.py",
      "line": 15,
      "pattern": "secrets",
      "severity": "review",
      "snippet": "import secrets as pysecrets"
    },
    {
      "file": "src/wsim/agent/dialogue_async.py",
      "line": 222,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "started = time.perf_counter()"
    },
    {
      "file": "src/wsim/agent/dialogue_async.py",
      "line": 228,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "round(time.perf_counter() - started, 4),"
    },
    {
      "file": "src/wsim/agent/dialogue_async.py",
      "line": 236,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "round(time.perf_counter() - started, 4),"
    },
    {
      "file": "src/wsim/agent/dialogue_async.py",
      "line": 273,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "started = time.perf_counter()"
    },
    {
      "file": "src/wsim/agent/dialogue_async.py",
      "line": 291,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "latency = round(time.perf_counter() - started, 4)"
    },
    {
      "file": "src/wsim/agent/dialogue_async.py",
      "line": 358,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "started = time.perf_counter()"
    },
    {
      "file": "src/wsim/agent/dialogue_async.py",
      "line": 381,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "round(time.perf_counter() - started, 4),"
    },
    {
      "file": "src/wsim/agent/dialogue_async.py",
      "line": 392,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "round(time.perf_counter() - started, 4),"
    },
    {
      "file": "src/wsim/agent/dialogue_async.py",
      "line": 10,
      "pattern": "uuid",
      "severity": "review",
      "snippet": "import uuid"
    },
    {
      "file": "src/wsim/agent/dialogue_async.py",
      "line": 178,
      "pattern": "uuid",
      "severity": "review",
      "snippet": "request_id = f\"dlg-{uuid.uuid4().hex[:10]}\""
    },
    {
      "file": "src/wsim/agent/dialogue_async.py",
      "line": 251,
      "pattern": "uuid",
      "severity": "review",
      "snippet": "request_id = f\"dlg-{uuid.uuid4().hex[:10]}\""
    },
    {
      "file": "src/wsim/agent/dialogue_async.py",
      "line": 329,
      "pattern": "uuid",
      "severity": "review",
      "snippet": "request_id = f\"dlg-{uuid.uuid4().hex[:10]}\""
    },
    {
      "file": "src/wsim/agent/provider.py",
      "line": 557,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "call_started = time.perf_counter()"
    },
    {
      "file": "src/wsim/agent/provider.py",
      "line": 593,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "last_error[\"latency_s\"] = time.perf_counter() - call_started"
    },
    {
      "file": "src/wsim/agent/provider.py",
      "line": 698,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "started = time.perf_counter()"
    },
    {
      "file": "src/wsim/agent/provider.py",
      "line": 701,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "attempt_latency = time.perf_counter() - started"
    },
    {
      "file": "src/wsim/agent/provider.py",
      "line": 702,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "call_latency = time.perf_counter() - call_started"
    },
    {
      "file": "src/wsim/agent/provider.py",
      "line": 721,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "attempt_latency = time.perf_counter() - started"
    },
    {
      "file": "src/wsim/agent/provider.py",
      "line": 722,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "call_latency = time.perf_counter() - call_started"
    },
    {
      "file": "src/wsim/agent/provider.py",
      "line": 744,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "call_latency = time.perf_counter() - call_started"
    },
    {
      "file": "src/wsim/agent/provider.py",
      "line": 752,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "call_latency = time.perf_counter() - call_started"
    },
    {
      "file": "src/wsim/agent/provider.py",
      "line": 903,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "started = time.perf_counter()"
    },
    {
      "file": "src/wsim/agent/provider.py",
      "line": 940,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "self.wall_time_s += time.perf_counter() - started"
    },
    {
      "file": "src/wsim/api/auth.py",
      "line": 14,
      "pattern": "secrets",
      "severity": "review",
      "snippet": "import secrets"
    },
    {
      "file": "src/wsim/api/auth.py",
      "line": 169,
      "pattern": "secrets",
      "severity": "review",
      "snippet": "salt_hex = secrets.token_bytes(16).hex()"
    },
    {
      "file": "src/wsim/api/auth.py",
      "line": 193,
      "pattern": "secrets",
      "severity": "review",
      "snippet": "hmac.compare_digest(secrets.token_bytes(32), secrets.token_bytes(32))"
    },
    {
      "file": "src/wsim/api/auth.py",
      "line": 193,
      "pattern": "secrets",
      "severity": "review",
      "snippet": "hmac.compare_digest(secrets.token_bytes(32), secrets.token_bytes(32))"
    },
    {
      "file": "src/wsim/api/auth.py",
      "line": 304,
      "pattern": "secrets",
      "severity": "review",
      "snippet": "token = secrets.token_urlsafe(32)"
    },
    {
      "file": "src/wsim/api/auth.py",
      "line": 305,
      "pattern": "secrets",
      "severity": "review",
      "snippet": "session_id = \"s:\" + secrets.token_hex(16)"
    },
    {
      "file": "src/wsim/api/auth.py",
      "line": 373,
      "pattern": "secrets",
      "severity": "review",
      "snippet": "new_token = secrets.token_urlsafe(32)"
    },
    {
      "file": "src/wsim/api/health.py",
      "line": 32,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "return time.perf_counter()"
    },
    {
      "file": "src/wsim/api/server.py",
      "line": 506,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "started = time.perf_counter()"
    },
    {
      "file": "src/wsim/api/server.py",
      "line": 652,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "path, int(getattr(self, \"_status_code\", 0) or 0), time.perf_counter() - started"
    },
    {
      "file": "src/wsim/api/server.py",
      "line": 727,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "started = time.perf_counter()"
    },
    {
      "file": "src/wsim/sim/headless.py",
      "line": 167,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "started_at = time.perf_counter()"
    },
    {
      "file": "src/wsim/sim/headless.py",
      "line": 1279,
      "pattern": "time.perf_counter",
      "severity": "review",
      "snippet": "\"wall_time_s\": round(time.perf_counter() - started_at, 3),"
    }
  ],
  "critical": [],
  "critical_count": 0
}
```

### ledger_no_model_text · 账本无模型文本 — PASS

模型台词只在旁路；事件库与状态均只含确定性模板句

```json
{
  "aside_preview": "XKXAUDITMODELMARKER 模型配音今日尚可，街口风大，早些回。",
  "event_seq": 7,
  "model_in_aside": true,
  "model_in_events": false,
  "model_in_state": false,
  "npc": "duan_wudou",
  "reply_is_template": true,
  "template_present": true,
  "template_preview": "我在呢，屠户这行我干惯了，从早忙到晚；你有话直说，我不爱绕弯子。金掌柜那三百文，我段五斗认！"
}
```

### golden_baseline_untouched · 旧世界 golden 基线未被静默改动 — FAIL

不可追溯/未复现：未提交 0 组、理由缺失 1 条、新鲜 run 漂移 无、dialogue 基线漂移 无

```json
{
  "baseline_additions": [
    {
      "baseline": [
        "GOLDEN"
      ],
      "commit": "60e85965602a41fc3c0b697aadbc50b9c3a02580",
      "has_reason": true,
      "subject": "P3-E R2: 披露 fake run 世界改写、对外改用含剪枝 K6、补 golden 与全量拦截审计"
    },
    {
      "baseline": [
        "DIALOGUE_GOLDEN"
      ],
      "commit": "cc3a4752ae320e75c78bc8b7a08e64f6d28b50fc",
      "has_reason": true,
      "subject": "test(dialogue): 锁定 dialogue 开启路径 golden 基线"
    },
    {
      "baseline": [
        "DIALOGUE_GOLDEN_P3I",
        "GOLDEN_P3I"
      ],
      "commit": "20dc739155884b4a0305e1dd723d1e8e9288952c",
      "has_reason": true,
      "subject": "feat(content): P3I 扩充世界内容与可玩深度"
    },
    {
      "baseline": [
        "DIALOGUE_GOLDEN_P3I_PRICE",
        "GOLDEN_P3I_PRICE"
      ],
      "commit": "64365f859f06d29c2c5a1d1c8fe75d3ab475d82d",
      "has_reason": true,
      "subject": "feat(pricing): 供需物价模型接入运行时"
    },
    {
      "baseline": [
        "DIALOGUE_GOLDEN_CALIB",
        "GOLDEN_CALIB"
      ],
      "commit": "5635c26c70d3a7328be156bd0300559aaa86c81a",
      "has_reason": true,
      "subject": "校准目标体量到 10 天有效产能：wealth 142 / mastery 0.033 / bond 12"
    },
    {
      "baseline": [
        "DIALOGUE_GOLDEN_MILESTONE",
        "GOLDEN_MILESTONE"
      ],
      "commit": "bc7013ce0e68ce8189d9379080dda1fa8396fc34",
      "has_reason": true,
      "subject": "feat(p4): 玩家成长线与 NPC 里程碑链/维护型目标"
    },
    {
      "baseline": [
        "DIALOGUE_GOLDEN_MILESTONE_V2",
        "GOLDEN_MILESTONE_V2"
      ],
      "commit": "6189ad94fd9ba92f428ce864251669326ac93566",
      "has_reason": true,
      "subject": "test(golden): P4 里程碑基线迁移到 v2（goal_create 新增 secondary 字段）"
    },
    {
      "baseline": [
        "DIALOGUE_GOLDEN_MILESTONE_V3",
        "GOLDEN_MILESTONE_V3"
      ],
      "commit": "e1d6d2bbccb0404b5859ebe6d80db0380f749e61",
      "has_reason": true,
      "subject": "fix(sim): 委托公平轮转 + 接单提前，盘活零接单模板"
    },
    {
      "baseline": [
        "DIALOGUE_GOLDEN_MILESTONE_V4",
        "GOLDEN_MILESTONE_V4"
      ],
      "commit": "240e5a8dc3184c34b14cd2bfa2bb9358d30ed8e2",
      "has_reason": true,
      "subject": "feat(api): 打通玩家 buy/sell/consume/escort，暴露价格与可读原因码"
    },
    {
      "baseline": [
        "DIALOGUE_GOLDEN_MILESTONE_V5",
        "GOLDEN_MILESTONE_V5"
      ],
      "commit": "1468511991fd5ac4ba03ae0e2be6950f7f17bb45",
      "has_reason": true,
      "subject": "feat(business): 开店经营闭环：初始库存、店铺雇佣、独立日结、自雇不记债"
    },
    {
      "baseline": [
        "DIALOGUE_GOLDEN_MILESTONE_V6",
        "GOLDEN_MILESTONE_V6"
      ],
      "commit": "0fa9e02926b225bd37fdbf06219994b0ca9fd0f7",
      "has_reason": true,
      "subject": "修复 NPC 多跳寻路：确定性最短路逐跳 + 移动/动作同 tick"
    }
  ],
  "baselines_count": 20,
  "current_baselines": [
    "DIALOGUE_GOLDEN",
    "DIALOGUE_GOLDEN_CALIB",
    "DIALOGUE_GOLDEN_MILESTONE",
    "DIALOGUE_GOLDEN_MILESTONE_V2",
    "DIALOGUE_GOLDEN_MILESTONE_V3",
    "DIALOGUE_GOLDEN_MILESTONE_V4",
    "DIALOGUE_GOLDEN_MILESTONE_V5",
    "DIALOGUE_GOLDEN_MILESTONE_V6",
    "DIALOGUE_GOLDEN_P3I",
    "DIALOGUE_GOLDEN_P3I_PRICE",
    "GOLDEN",
    "GOLDEN_CALIB",
    "GOLDEN_MILESTONE",
    "GOLDEN_MILESTONE_V2",
    "GOLDEN_MILESTONE_V3",
    "GOLDEN_MILESTONE_V4",
    "GOLDEN_MILESTONE_V5",
    "GOLDEN_MILESTONE_V6",
    "GOLDEN_P3I",
    "GOLDEN_P3I_PRICE"
  ],
  "dialogue_reproduction": {
    "actual": {
      "dialogue_calls": 0,
      "event_final": "0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49",
      "events_total": 791,
      "state_final": "6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55"
    },
    "baseline": "DIALOGUE_GOLDEN_MILESTONE_V6",
    "drift": {},
    "matched": true,
    "ran": true
  },
  "fresh_reproduction": {
    "actual": {
      "burned": 72,
      "business_cash": 13058,
      "buy_out": 657,
      "event_final": "0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49",
      "events_total": 791,
      "precondition_blocked": 5,
      "precondition_pruned": 2,
      "sell_in": 584,
      "state_final": "6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55"
    },
    "baseline": "GOLDEN_MILESTONE_V6",
    "drift": {},
    "matched": true,
    "ran": true
  },
  "history": [
    {
      "baselines": [
        "DIALOGUE_GOLDEN",
        "DIALOGUE_GOLDEN_CALIB",
        "DIALOGUE_GOLDEN_MILESTONE",
        "DIALOGUE_GOLDEN_MILESTONE_V2",
        "DIALOGUE_GOLDEN_P3I",
        "DIALOGUE_GOLDEN_P3I_PRICE",
        "GOLDEN",
        "GOLDEN_CALIB",
        "GOLDEN_MILESTONE",
        "GOLDEN_MILESTONE_V2",
        "GOLDEN_P3I",
        "GOLDEN_P3I_PRICE"
      ],
      "commit": "6189ad94fd9ba92f428ce864251669326ac93566",
      "subject": "test(golden): P4 里程碑基线迁移到 v2（goal_create 新增 secondary 字段）"
    },
    {
      "baselines": [
        "DIALOGUE_GOLDEN",
        "DIALOGUE_GOLDEN_CALIB",
        "DIALOGUE_GOLDEN_MILESTONE",
        "DIALOGUE_GOLDEN_MILESTONE_V2",
        "DIALOGUE_GOLDEN_MILESTONE_V3",
        "DIALOGUE_GOLDEN_P3I",
        "DIALOGUE_GOLDEN_P3I_PRICE",
        "GOLDEN",
        "GOLDEN_CALIB",
        "GOLDEN_MILESTONE",
        "GOLDEN_MILESTONE_V2",
        "GOLDEN_MILESTONE_V3",
        "GOLDEN_P3I",
        "GOLDEN_P3I_PRICE"
      ],
      "commit": "e1d6d2bbccb0404b5859ebe6d80db0380f749e61",
      "subject": "fix(sim): 委托公平轮转 + 接单提前，盘活零接单模板"
    },
    {
      "baselines": [
        "DIALOGUE_GOLDEN",
        "DIALOGUE_GOLDEN_CALIB",
        "DIALOGUE_GOLDEN_MILESTONE",
        "DIALOGUE_GOLDEN_MILESTONE_V2",
        "DIALOGUE_GOLDEN_MILESTONE_V3",
        "DIALOGUE_GOLDEN_MILESTONE_V4",
        "DIALOGUE_GOLDEN_P3I",
        "DIALOGUE_GOLDEN_P3I_PRICE",
        "GOLDEN",
        "GOLDEN_CALIB",
        "GOLDEN_MILESTONE",
        "GOLDEN_MILESTONE_V2",
        "GOLDEN_MILESTONE_V3",
        "GOLDEN_MILESTONE_V4",
        "GOLDEN_P3I",
        "GOLDEN_P3I_PRICE"
      ],
      "commit": "240e5a8dc3184c34b14cd2bfa2bb9358d30ed8e2",
      "subject": "feat(api): 打通玩家 buy/sell/consume/escort，暴露价格与可读原因码"
    },
    {
      "baselines": [
        "DIALOGUE_GOLDEN",
        "DIALOGUE_GOLDEN_CALIB",
        "DIALOGUE_GOLDEN_MILESTONE",
        "DIALOGUE_GOLDEN_MILESTONE_V2",
        "DIALOGUE_GOLDEN_MILESTONE_V3",
        "DIALOGUE_GOLDEN_MILESTONE_V4",
        "DIALOGUE_GOLDEN_MILESTONE_V5",
        "DIALOGUE_GOLDEN_P3I",
        "DIALOGUE_GOLDEN_P3I_PRICE",
        "GOLDEN",
        "GOLDEN_CALIB",
        "GOLDEN_MILESTONE",
        "GOLDEN_MILESTONE_V2",
        "GOLDEN_MILESTONE_V3",
        "GOLDEN_MILESTONE_V4",
        "GOLDEN_MILESTONE_V5",
        "GOLDEN_P3I",
        "GOLDEN_P3I_PRICE"
      ],
      "commit": "1468511991fd5ac4ba03ae0e2be6950f7f17bb45",
      "subject": "feat(business): 开店经营闭环：初始库存、店铺雇佣、独立日结、自雇不记债"
    },
    {
      "baselines": [
        "DIALOGUE_GOLDEN",
        "DIALOGUE_GOLDEN_CALIB",
        "DIALOGUE_GOLDEN_MILESTONE",
        "DIALOGUE_GOLDEN_MILESTONE_V2",
        "DIALOGUE_GOLDEN_MILESTONE_V3",
        "DIALOGUE_GOLDEN_MILESTONE_V4",
        "DIALOGUE_GOLDEN_MILESTONE_V5",
        "DIALOGUE_GOLDEN_P3I",
        "DIALOGUE_GOLDEN_P3I_PRICE",
        "GOLDEN",
        "GOLDEN_CALIB",
        "GOLDEN_MILESTONE",
        "GOLDEN_MILESTONE_V2",
        "GOLDEN_MILESTONE_V3",
        "GOLDEN_MILESTONE_V4",
        "GOLDEN_MILESTONE_V5",
        "GOLDEN_P3I",
        "GOLDEN_P3I_PRICE"
      ],
      "commit": "c3980c96b66e5f580587fc6efafff96342876f2d",
      "subject": "fix(intent): 玩家店铺雇工进入日程：契约即出工，欠薪仍到店"
    },
    {
      "baselines": [
        "DIALOGUE_GOLDEN",
        "DIALOGUE_GOLDEN_CALIB",
        "DIALOGUE_GOLDEN_MILESTONE",
        "DIALOGUE_GOLDEN_MILESTONE_V2",
        "DIALOGUE_GOLDEN_MILESTONE_V3",
        "DIALOGUE_GOLDEN_MILESTONE_V4",
        "DIALOGUE_GOLDEN_MILESTONE_V5",
        "DIALOGUE_GOLDEN_MILESTONE_V6",
        "DIALOGUE_GOLDEN_P3I",
        "DIALOGUE_GOLDEN_P3I_PRICE",
        "GOLDEN",
        "GOLDEN_CALIB",
        "GOLDEN_MILESTONE",
        "GOLDEN_MILESTONE_V2",
        "GOLDEN_MILESTONE_V3",
        "GOLDEN_MILESTONE_V4",
        "GOLDEN_MILESTONE_V5",
        "GOLDEN_MILESTONE_V6",
        "GOLDEN_P3I",
        "GOLDEN_P3I_PRICE"
      ],
      "commit": "0fa9e02926b225bd37fdbf06219994b0ca9fd0f7",
      "subject": "修复 NPC 多跳寻路：确定性最短路逐跳 + 移动/动作同 tick"
    },
    {
      "baselines": [
        "DIALOGUE_GOLDEN",
        "DIALOGUE_GOLDEN_CALIB",
        "DIALOGUE_GOLDEN_MILESTONE",
        "DIALOGUE_GOLDEN_MILESTONE_V2",
        "DIALOGUE_GOLDEN_MILESTONE_V3",
        "DIALOGUE_GOLDEN_MILESTONE_V4",
        "DIALOGUE_GOLDEN_MILESTONE_V5",
        "DIALOGUE_GOLDEN_MILESTONE_V6",
        "DIALOGUE_GOLDEN_P3I",
        "DIALOGUE_GOLDEN_P3I_PRICE",
        "GOLDEN",
        "GOLDEN_CALIB",
        "GOLDEN_MILESTONE",
        "GOLDEN_MILESTONE_V2",
        "GOLDEN_MILESTONE_V3",
        "GOLDEN_MILESTONE_V4",
        "GOLDEN_MILESTONE_V5",
        "GOLDEN_MILESTONE_V6",
        "GOLDEN_P3I",
        "GOLDEN_P3I_PRICE"
      ],
      "commit": "aa885b1715f342521c68b453588e5ea2c72e3b73",
      "subject": "fix(dialogue): 修复 NPC 对话同质化（轮次/人格入模板 + 模型双路接入）"
    },
    {
      "baselines": [
        "DIALOGUE_GOLDEN",
        "DIALOGUE_GOLDEN_CALIB",
        "DIALOGUE_GOLDEN_MILESTONE",
        "DIALOGUE_GOLDEN_MILESTONE_V2",
        "DIALOGUE_GOLDEN_MILESTONE_V3",
        "DIALOGUE_GOLDEN_MILESTONE_V4",
        "DIALOGUE_GOLDEN_MILESTONE_V5",
        "DIALOGUE_GOLDEN_MILESTONE_V6",
        "DIALOGUE_GOLDEN_P3I",
        "DIALOGUE_GOLDEN_P3I_PRICE",
        "GOLDEN",
        "GOLDEN_CALIB",
        "GOLDEN_MILESTONE",
        "GOLDEN_MILESTONE_V2",
        "GOLDEN_MILESTONE_V3",
        "GOLDEN_MILESTONE_V4",
        "GOLDEN_MILESTONE_V5",
        "GOLDEN_MILESTONE_V6",
        "GOLDEN_P3I",
        "GOLDEN_P3I_PRICE"
      ],
      "commit": "1e076da0e5b54a5fc403b5dae27f358e4befc28c",
      "subject": "refactor(dialogue): 模型台词统一到 dialogue_aside 单一路径"
    }
  ],
  "history_commits": 18,
  "in_place_value_changes": [
    {
      "baseline": "GOLDEN",
      "changed_keys": [
        "event_final",
        "state_final"
      ],
      "commit": "98f0cdfee955303357a0c6ae85302b2f85ae37ad",
      "has_reason": true,
      "new": {
        "event_final": "9f2547cc85a4f89476ac947085d1a5ce9f6bd3e35b80561baa9569a99f3b7766",
        "state_final": "38bdacdaa2764374ac0b59a27eab5e84788b58ba84d7e210b6915384d5f220e5"
      },
      "old": {
        "event_final": "4d4019291eaaeb6b58c5279dce7840efcaaa4622272223782bbb84ab778f35cf",
        "state_final": "1bd870c4c8270da6117721f67a67a38bbc2d34dae12386a0d16f0ce3abedd226"
      },
      "subject": "fix(dialogue): 重写 fallback 为 20–60 字第一人称短回应"
    },
    {
      "baseline": "GOLDEN",
      "changed_keys": [
        "event_final",
        "state_final"
      ],
      "commit": "e2151e0d03871571fd9119764b3de9647ff2d6f0",
      "has_reason": true,
      "new": {
        "event_final": "43e16d6b765561ed18889d533eb6d46e70192441d1ecb74e5208eb80d5c75263",
        "state_final": "9d73cb2e87c52cfe9e8880ba704f957825288e95aaccdefc02725f98f08ed894"
      },
      "old": {
        "event_final": "9f2547cc85a4f89476ac947085d1a5ce9f6bd3e35b80561baa9569a99f3b7766",
        "state_final": "38bdacdaa2764374ac0b59a27eab5e84788b58ba84d7e210b6915384d5f220e5"
      },
      "subject": "test(golden): 新建基线 v2（意图分化导致哈希漂移）"
    },
    {
      "baseline": "GOLDEN",
      "changed_keys": [
        "precondition_pruned"
      ],
      "commit": "335a4ee0153611439bbb6912d7e59294b2006755",
      "has_reason": true,
      "new": {
        "precondition_pruned": 2
      },
      "old": {
        "precondition_pruned": 5
      },
      "subject": "feat(a3): 竞态 trade 记 stale、intent 归零与护栏收口"
    },
    {
      "baseline": "DIALOGUE_GOLDEN",
      "changed_keys": [
        "event_final",
        "state_final"
      ],
      "commit": "d780dcff3ce41a5b82a30b84f260a48b4728bd63",
      "has_reason": false,
      "new": {
        "event_final": "c04895352f525d7ecaf6aa4175f987aacea7e0d600fd3a7fd03d31513f7dbb8c",
        "state_final": "b980558ee70f752127185a93fc52eece0593f0daa23598ba6a2bcf625253987c"
      },
      "old": {
        "event_final": "86946e7c66012dcd2c51ed65f1004752eae165742d050431e2d5fb3b80e1a6a0",
        "state_final": "a52d5a482f5708298fb3be5c863df8a777428716e67cd811644b674d16565b96"
      },
      "subject": "feat(dialogue): 接入 text_guard 与统一 system prompt"
    },
    {
      "baseline": "DIALOGUE_GOLDEN_MILESTONE_V6",
      "changed_keys": [
        "event_final",
        "state_final"
      ],
      "commit": "aa885b1715f342521c68b453588e5ea2c72e3b73",
      "has_reason": true,
      "new": {
        "event_final": "0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49",
        "state_final": "6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55"
      },
      "old": {
        "event_final": "e98e395ad6647a264f9ec615c502f73a11a84d2a9cdbe8d7d2af80c5abf64367",
        "state_final": "e4a6b5f7ee645331ac281a69891e0ff7f28bb220eb2f8668489b7e774b99a626"
      },
      "subject": "fix(dialogue): 修复 NPC 对话同质化（轮次/人格入模板 + 模型双路接入）"
    },
    {
      "baseline": "GOLDEN_MILESTONE_V6",
      "changed_keys": [
        "event_final",
        "state_final"
      ],
      "commit": "aa885b1715f342521c68b453588e5ea2c72e3b73",
      "has_reason": true,
      "new": {
        "event_final": "0c15b0d8892e52b89f4d9e022fd7fabde6b5283b4aa8c924e3bc5760d8a30d49",
        "state_final": "6be665b533b778f2afb7d4a3fd57cd5ff6dc968509c11d238d40d5fed5870a55"
      },
      "old": {
        "event_final": "3be92716396b88d086571e40968581806549240ecaf6a13ca2debd7c317c16e4",
        "state_final": "03b784697bf9de1c2db60d0925f11ebde4d3d956e2e7a188842e4c760915b8c7"
      },
      "subject": "fix(dialogue): 修复 NPC 对话同质化（轮次/人格入模板 + 模型双路接入）"
    },
    {
      "baseline": "DIALOGUE_GOLDEN_MILESTONE_V6",
      "changed_keys": [
        "dialogue_calls"
      ],
      "commit": "1e076da0e5b54a5fc403b5dae27f358e4befc28c",
      "has_reason": true,
      "new": {
        "dialogue_calls": 0
      },
      "old": {
        "dialogue_calls": 65
      },
      "subject": "refactor(dialogue): 模型台词统一到 dialogue_aside 单一路径"
    }
  ],
  "traceability_failures": [
    {
      "baseline": "DIALOGUE_GOLDEN",
      "changed_keys": [
        "event_final",
        "state_final"
      ],
      "commit": "d780dcff3ce41a5b82a30b84f260a48b4728bd63",
      "has_reason": false,
      "new": {
        "event_final": "c04895352f525d7ecaf6aa4175f987aacea7e0d600fd3a7fd03d31513f7dbb8c",
        "state_final": "b980558ee70f752127185a93fc52eece0593f0daa23598ba6a2bcf625253987c"
      },
      "old": {
        "event_final": "86946e7c66012dcd2c51ed65f1004752eae165742d050431e2d5fb3b80e1a6a0",
        "state_final": "a52d5a482f5708298fb3be5c863df8a777428716e67cd811644b674d16565b96"
      },
      "subject": "feat(dialogue): 接入 text_guard 与统一 system prompt"
    }
  ],
  "uncommitted_changes": {}
}
```

### combat_redline · 战斗新红线 — PASS

1 场真实 fight（默认世界内存夹具）自洽：5 回合、事件效果/状态/log 一致、同 seed 重打一致

```json
{
  "action_files_with_combat": [
    "combat.py",
    "player.py"
  ],
  "event_hash": "4be8701dc37e8fbddc830f1c8eac2c09974697d45dfa2761211032a75991a702",
  "event_seq": 7,
  "events_total": 8,
  "fixture_note": "默认 NPC max_hp=0；审计在内存给同房 NPC/玩家固定战斗属性，走真实 action/writer/PRNG",
  "headless_fight_events": 0,
  "log_hp": {
    "duan_wudou": 0,
    "player:audit_fighter": 98
  },
  "post": {
    "hp": {
      "duan_wudou": 0,
      "player:audit_fighter": 98
    },
    "status": {
      "duan_wudou": "dead",
      "player:audit_fighter": "alive"
    }
  },
  "pre": {
    "hp": {
      "duan_wudou": 70,
      "player:audit_fighter": 120
    },
    "status": {
      "duan_wudou": "alive",
      "player:audit_fighter": "alive"
    }
  },
  "problems": [],
  "replay_consistent": true,
  "rounds_len": 5,
  "rounds_used": 5,
  "runs_skipped": false,
  "same_seed_refight": true,
  "state_hash_post": "df743f5efc8aa4faa596a413b04af145ecc5d8aabbcac11cadcf0ffe9e6cde6b",
  "state_hash_pre": "83e5adfd580d0fe0c0ffc7e9508bce61d77a1a808cc8567426404a28fa03418f",
  "winner": "attacker",
  "xkx_packs": []
}
```

