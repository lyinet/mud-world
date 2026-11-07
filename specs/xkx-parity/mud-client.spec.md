# xkx 文字壳「MUD 客户端」界面补齐 Spec

范围：`xkx-parity` 分支。只改 API 只读适配层、文字壳、前端与测试；不碰
`src/wsim/sim/**`、`src/wsim/actions/**`、`src/wsim/world/**`、`tools/**`、`content/**`。

## 1. 目标与铁律

把现在「一条日志流 + 3×3 舆图」的页面补成 MUD 客户端：人物、技能、装备、行囊、
地面、分区舆图、help、命令补全。铁律：

1. 所有人物/技能/装备/任务/负重/探索数据由 `/api/player/state` 提供，前端不推导、不缓存游戏状态；
   引擎尚未透出的字段（如 `region`）由后端给 null + 原因，前端只显示原因。
2. 不可用项不消失：固定显示「未开放 / 暂不可用」+ 后端给出的原因。
3. 不画假全图：舆图只画当前房间 + 一跳可达出口（来自真实内容包）。
4. help 状态原样消费 `content/xkx_help`：`已实现 / 待实现 / 不做`，待实现绝不写成可用。
5. 每项在真实 xkx 世界（8011/8041）验证并截图；显式路径 `git add`，推 `origin xkx-parity`。

## 2. 现状证据

- `/api/player/state` 实现在 [session.py](../../src/wsim/api/session.py) `player_state()`；
  `player` 目前没有 hp/max_hp、attrs、combat_exp、负重。
- `Entity`（`src/wsim/world/state.py`）已有 `hp/max_hp/attrs/combat_exp/skill_levels/skill_xp`；
  无内力（neili）、无负重上限字段。
- `content/xkx/items.json` 每件物品有 `weight`（内容包原值）与 `equip_slot`。
- `_skills_detail`（legacy 0..1 熟练度→1..10 级）、`_equipment_view`、`_wallet_view` 已存在。
- `content/xkx_help/commands.yaml`：153 条命令，状态 已实现 40 / 待实现 51 / 不做 62；
  `topics.yaml`：32 个主题，状态 开放 / 部分开放 / 未开放 / 管理。
- 无「已探索」记录：需在 API 只读层从事件日志 `set_location` 效果重建，并补当前房间。
- 前端只有日志流；`ui/xkx/app.js` 已有 `sendCommand/renderView/renderMap`。
- xkx 世界当前后台运行在 8011/8041。

## 3. 后端变更

### 3.1 `src/wsim/api/session.py::player_state()` 增补只读字段

`player` 字典新增（均为只增不改，`PROFILE_VERSION` 保持 1）：

```jsonc
"vitals": {
  "hp": 120, "max_hp": 120, "hp_pct": 100,   // ent.max_hp==0 时全部 null
  "hp_reason": "",                            // 无气血时的原因
  "neili": null,                              // 引擎无内力字段，永远 null
  "neili_reason": "引擎尚无内力（neili）字段，暂未开放。"
},
"combat_exp": 0,                              // ent.combat_exp
"attrs": {"str": 20, "int": 15, "...": 0},    // ent.attrs 原样
"attrs_detail": [                             // 固定 9 项：str/int/con/dex/spi/sta/kar/cps/cor
  {"key": "str", "label": "膂力", "value": 20},
  {"key": "int", "label": "悟性", "value": 15}
],                                            // 缺失值为 null
"attrs_reason": "",                           // ent.attrs 为空时的原因
"weight": {
  "current": 0,                               // 行囊 + 已装备，按内容包 weight 求和（后端算）
  "max": null,                                // 引擎无负重上限
  "unit": "内容包原值",
  "reason": "引擎没有负重上限字段，只显示当前负重。"
},
"skills_engine": [                            // ent.skill_levels/skill_xp（xkx 等级制）
  {"name": "basic", "level": 3, "xp": 12}
],
"skills_status": {"available": false, "reason": "当前角色还没有已学技能；引擎的 skills/practice 接入后自动显示。"},
"exploration": {
  // region 由引擎线补字段；内容包没有该字段时必须为 null + 原因，禁止按地点 id 猜区域。
  "area": {"id": "hangzhou", "name": "杭州"},   // 无 region 时 null
  "area_total": 118, "area_explored": 1,         // 无 region 时 null
  "area_reason": "",                             // 无 region 时的原因
  "rooms_total": 195, "rooms_explored": 1
}
```

实现要点：

- `_skills_xkx_view(ent)`：读 `ent.skill_levels` / `ent.skill_xp`。
- `_weight_view(ent)`：`inventory` 每件 `qty * content.items[item].weight`，再加已装备每槽 1 件；
  `weight` 缺失按 0，不猜。
- `_exploration_view(pid, loc_id)`：惰性建 `self._visited: dict[str,set[str]]`，只扫一次
  `self.store.read_all()`，取 `outcome=="ok"`、`actor.startswith("player:")` 的
  `set_location` 效果 `data.to`；之后每次把当前 `loc_id` 加进去。
- **区域只读消费引擎 `region` 字段**（`content.locations[loc].get("region")` 或引擎状态里的
  region）：有则给 `area/area_total/area_explored`；没有则三者全 `null`，并给
  `area_reason = "引擎/内容包尚未提供 region 字段，区域名与分区计数暂不可用。"`。
  严禁按地点 id 前缀推断区域，也不允许前端补算。
- `ground` 每行增补 `name`（`_item_name`），使前端只读 state 也能显示中文名。

### 3.2 任务面板数据

`player_state.player` 已含 `active_quests` / `active_quests_detail`；本任务原样透出到前端任务面板：
有任务则逐条显示，无任务显示「当前没有进行中的任务」；若引擎字段缺失显示「暂不可用 + 原因」，
不做假按钮。

### 3.3 装备可用性

`player_state.player.equipment` 增加：

```jsonc
"actions": {
  "equip": false, "unequip": false,
  "reason": "引擎动作已注册，但文字壳尚未接入穿脱命令。" // 未注册则写引擎未注册
}
```

`reason` 由 `self.registry.get("equip"/"unequip")` 决定，前端据此显示常驻说明，不隐藏。

### 3.4 新增只读接口 `GET /api/xkx/help`（`src/wsim/api/server.py`）

- `_do_GET` 在 `_WORLD_MAP` 分支旁直接返回 `session.xkx_help_catalog()`，匿名可读、不含玩家数据。
- `WorldSession.xkx_help_catalog()` 复用同一个 `XKXShell` 实例（抽出 `_xkx_shell_instance()`）。
- 返回：

```jsonc
{
  "ok": true,
  "counts": {"commands": 153, "topics": 32,
             "status": {"已实现": 40, "待实现": 51, "不做": 62}},
  "commands": [{"name":"look","aliases":["看"],"params":"—","summary":"…",
                "system":"系统","status":"已实现","blocker":"","evidence":"…",
                "shell":true,"shell_verb":"look"}],
  "topics": [{"id":"newbie","title":"…","category":"入门","status":"开放"}],
  "shell_verbs": ["look","go","ask","buy","sell","fight","loot","score","inventory","help"]
}
```

`shell=true` 仅当该命令名或任一别名被 `VERB_ALIASES` 接受（`get→loot` 也算）。

## 4. 文字壳 help 与未知命令（`src/wsim/api/xkx_shell.py`）

初始化时从 `Path(session.content_root)/xkx_help/` 读 `commands.yaml`、`topics.yaml`、
`manifest.json`（只读）。

`help` 分支：

- `help`：总览：153 命令按状态计数（40/51/62）、32 主题、本壳已接入口清单（10 个）。
- `help 命令`：7 个系统分类计数 + 「help 命令 <系统或关键词>」提示。
- `help 命令 战斗` / `help 命令 <关键词>`：逐条列 `name（别名） 状态：原因`。
- `help <命令名/别名>`：摘要、参数、状态、blocker/evidence；
  再加一行「本文字壳：已接入口 / 未接入口（只能看到说明）」。
- `help 主题`：按分类列 32 主题及状态。
- `help <主题 id 或标题关键词>`：`开放/部分开放/未开放` 显示正文；`管理` 只显示标题与
  「管理向内容，不向玩家开放」，不贴正文。
- 未知帮助词：列出相近命令/主题，不臆造。

未知命令（`execute` 两处兜底 + 裸输入）：

- 先在 help 里精确匹配命令/别名：
  - `待实现` → `「X」还没接上：待实现（原因：blocker）。`
  - `不做` → `「X」不做：blocker。`
- 否则 `difflib` 在 shell 动词 + help 命令/别名里找相近项：
  `你没听懂「X」。你是想打「Y」吗？输入 help 看全部命令。`

## 5. 前端（`ui/xkx/`）

### 5.1 页面结构（`index.html`）

右侧面板顺序：

1. 人物（`#character`）
2. 此地舆图（`#map-meta` + `#map` + `#blocked`）
3. 技能（`#skills`）
4. 装备（`#equipment` + `#equip-note`）
5. 任务（`#quests`）
6. 行囊（`#inventory`）
7. 地面（`#ground`）
8. 在场人物（`#npcs`）
9. 货架（`#goods`）
10. 快捷（`#quick` + `#world-stats`）

输入框下新增 `#complete` 补全列表；顶栏新增气血 pill `#pill-hp`。

### 5.2 数据流（`app.js`）

- 页面启动与每条命令结束后：`GET /api/player/state?account_id=...` → `renderState(state)`。
- `renderState` 只读字段：
  - 人物：气血条宽用后端 `vitals.hp_pct`；`hp==null` 显示「未开放（hp_reason）」；
    内力恒「未开放（neili_reason）」；经验 `combat_exp`；属性 `attrs_detail`（null 显示「—」，
    全空显示 `attrs_reason`）；金钱 `wallet.money`；负重 `weight.current / max`，
    `max==null` 显示「上限未开放（reason）」。
  - 技能：优先 `skills_engine`（`等级 · xp`），否则 `skills_detail`（`level 级`）；
    `skills_status.available=false` → 「暂不可用（reason）」，面板不消失。
  - 装备：`equipment.slots` 全列（空槽显示「空」）；`equipment.actions.reason` 常驻显示
    「穿脱：暂不可用（…）」，不渲染可点按钮。
  - 任务：`active_quests_detail` / `active_quests`；无任务「当前没有进行中的任务」，
    字段缺失「暂不可用（原因）」，不渲染假按钮。
  - 行囊：`player.wallet.items`；点击填 `sell <name> `。
  - 地面：`ground`（含 name）；点击直接 `get <name>`；空则「地上没有东西。」。
  - 舆图标题：`area` 有值时显示 `区域 · 本区已探索 area_explored/area_total ·
    全图 rooms_explored/rooms_total`；`area==null` 时显示
    「区域：暂不可用（area_reason）· 全图 rooms_explored/rooms_total」，不猜区域。
- 舆图仍用 `/api/xkx/command` 返回的 `view.exits`（真实方向/可达/原因），点击发 `go <direction>`。
- state 请求失败：各面板显示「状态暂不可用（错误）」，不隐藏。

### 5.3 命令补全

- 启动时 `GET /api/xkx/help` 缓存静态目录（非游戏状态）。
- 输入首词前缀匹配命令名/别名（不区分大小写），最多 8 条，shell 优先。
- 每条显示 `name · status · summary`；`待实现/不做` 用弱化色并带 `title=blocker`。
- Tab：多个匹配时补最长公共前缀，唯一匹配补全并加空格。
- ↑/↓：补全列表可见时移动选中；否则沿用历史命令。
- Enter：正常提交；点击建议项填入。
- 位置：`.cmd` 下方绝对定位 `#complete`。

### 5.4 样式（`style.css`）

暗色江湖风格不变，新增：`.stat-grid/.stat/.bar/.bar-fill/.slots/.slot/.note/.map-meta/#complete`，
移动端沿用现有单列断点。

## 6. 测试

`tests/test_xkx_api.py` 新增：

1. `test_player_state_exposes_vitals_attrs_exp_weight`：字段存在；`hp/max_hp` 与
   `view.player` 一致；`attrs_detail` 含 6 项；`weight.current` 等于按内容包 weight 手算；
   `neili is None` 且带原因。
2. `test_player_state_reports_skills_or_reason`：有 `skills_engine/skills_detail` 或
   `skills_status.available=false` + 非空 reason，二选一必须成立。
3. `test_exploration_counts_grow_after_moves`：初始 `rooms_explored>=1`；
   走到新房间后计数增加；`rooms_total==195`；无 `region` 时 `area==null` 且
   `area_reason` 非空（有 `region` 时再断言本区计数）。
4. `test_help_catalog_has_153_commands_and_32_topics`：计数 153/32、状态 40/51/62；
   `baitan` 的 `shell=false`，`look` 的 `shell=true`。
5. `test_help_command_status_and_topic_body`：`help 命令 战斗` 含「待实现」与原因；
   `help 主题` 含 32 主题；`help 新手指南` 有正文；管理主题不贴正文。
6. `test_unknown_command_says_dont_understand`：`zzz` → 「没听懂」+ 建议；
   `baitan` → 「待实现」+ blocker，不写成可用。
7. `test_quests_panel_data_or_reason`：`active_quests_detail` 与 `active_quests` 存在，
   无任务时为空列表，不伪造任务。

回归：`pytest tests/test_xkx_api.py -q`，再跑既有 P3 套件。

## 7. 真实世界验收（8011/8041）

1. `lsof` 清理 8011/8041 旧进程，`./scripts/start_world.sh --world xkx --reset`。
2. Playwright（系统 python3）脚本：
   - 打开 `http://127.0.0.1:8011`，等 state 与面板渲染 → `xkx-panels.png`。
   - 保存 `/api/player/state` 原始 JSON 到 `/tmp/xkx-state.json`。
   - 输入 `n` 走一格 → 确认 `rooms_explored` 增加 → `xkx-map.png`。
   - 输入 `help` → `xkx-help.png`；`help 命令 战斗` → `xkx-help-commands.png`。
   - 输入 `lo` 触发补全 → `xkx-complete.png`。
   - 输入 `zzz` 回车 → 日志出现「没听懂」→ `xkx-unknown.png`。
3. 截图复制到 `/Users/lyinet/projects/XKX/`，回报原始响应片段。

## 8. 提交

- 先 `git fetch`；若本地 `xkx-parity` 落后，快进到 `origin/xkx-parity`。
- 显式 `git add` 上述文件（含 spec/tasks），中文提交说明（改了什么/为什么/验证），
  推 `origin xkx-parity`。不 force、不动他人未提交改动。
