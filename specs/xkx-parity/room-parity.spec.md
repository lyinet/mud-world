# 房间逐字段对拍规格（4004 原版 vs 复刻 195 房）

状态：执行中（用户已下工作单，无新增待确认项）
产出：`reports/xkx-ref/room-parity.md` + `reports/xkx-ref/room-parity.json`
范围：`tools/xkx_ref/**`、`reports/xkx-ref/**`、`specs/xkx-parity/room-parity.*`
禁止：改 LPC 逻辑；改 `src/**`、`ui/**`、`content/**`、`config/**`；动其他线路未提交改动。

## 1. 目标

用原版运行时 `look` 的**实际输出**，逐字段量出复刻 195 个房间的相似度：
房间名、长描述、出口列表、地面物品、在场 NPC。
输出可反复跑、可横向比的数字，而不是一次性的目测结论。

## 2. 数据来源

| 侧 | 来源 | 说明 |
|----|------|------|
| 原版 | 探针角色逐房改存档 `startroom` 直登后 `look` | 10 个代表房间在独立探针实例（同一 `run/mudlib` + 同一驱动）跑；4004 上另做 10 房对照，避免长时间占用 4004 |
| 复刻 | `content/xkx/rooms.json` / `exits.json` / `items.json` / `npcs.json` | 引擎内容包，即 `look` 渲染的数据源 |

探针实例：`run/mudlib-parity`（独立 data/log）+ `config-parity.ini`（端口 4020），与命中率采集（4010）隔离。
探针角色快照：`run/snapshots/xkxref.o` 的副本，`startroom` 设为 `/d/hangzhou/duanqiao`（非战斗字段），并加 `autoload ({"/clone/misc/fire"})` 以便夜间也能看到出口。

## 3. 归一化规则（必须逐条列进报告）

只允许以下规则，且**不删除任何描述主体字符**：

1. `N1` 去 ANSI 颜色码（原版有，复刻无）。
2. `N2` 提示符 `> `、频道行（`【谣言】`/`【风闻】`/`【存盘】`）丢弃。
3. `N3` 比较描述时折叠换行与行首缩进（`\s*\n\s*` → 空串）；非空白字符全部保留。
4. `N4` 室外房间的昼夜描述行（`NATURE_D` 的 `day_phase[*]["desc_msg"]`）丢弃，并单独计数；夜间“天色太黑了，你看不清明显的出路。”视为出口不可观察，不参与出口一致率。
5. `N5` 物品/NPC 行去掉数量前缀与英文括号，只保留中文名；按多重集合比较。
6. `N6` 房间名比较去掉首尾空白与 ` - ` 后缀。

## 4. 字段定义

| 字段 | 原版解析 | 复刻解析 |
|------|----------|----------|
| `name` | 首行 `<short> - ` 前半段 | `rooms.json.name` |
| `desc` | 名称行之后、出口行/昼夜行/物品行之前的正文 | `rooms.json.desc`（N3 后） |
| `exits` | “这里明显的出口是 …”/“这里唯一的出口是 …”/“这里没有任何明显的出路。” | `exits.json` 中 `from==room` 的 `dir` 集合 |
| `items` | `combined(inv)` 中判为物品的行（N5） | `objects[].kind=="item"` 经 `items.json` 解析出 `name` |
| `npcs` | `combined(inv)` 中判为 NPC 的行（N5） | `objects[].kind=="npc"` 经 `npcs.json` 解析出 `name` |

原版物品/NPC 区分：用原版源码 `d/**/npc/**` 的 `set_name` 名单做判定；未命中的条目记为 `unknown` 并计入报告（不静默归类）。

## 5. 差异分类

`name_mismatch` / `desc_missing`（复刻空或显著短）/ `desc_extra`（复刻多字）/ `desc_missing_text`（复刻少字）/ `exits_mismatch` / `items_mismatch` / `npcs_mismatch` / `unparsed`（原版文本无法解析）。
“多字/少字”用字符级 diff 统计增删字符数，给出前后各 20 字样例。

## 6. 执行顺序

1. 先跑 10 个代表房间（楼外楼、青石大道、断桥、白堤、孤山、灵隐寺、苏堤、六和塔底、岳坟、净慈寺），人工核验工具输出。
2. 工具跑通后再全量 195。
3. 4004 上的 10 房对照（临时快照副本探针，独立会话，用完即删）作为“同一 mudlib 下探针实例与原版一致”的对照，报告单列。
4. 导航用“逐房改 `startroom` 直登”，不按复刻出口图走路：出口差异会导致串房，把导航噪声误记为内容差异。

## 7. 验收

- `room-parity.json` 含逐房逐字段结果、汇总一致率、差异分类计数与样例。
- `room-parity.md` 列明全部归一化规则，并明确“哪些字段不可观察/未解析”。
- 工具可重复：`python3 tools/xkx_ref/parity_rooms.py --all --by-startroom`。
- 显式 `git add`，推 `origin xkx-parity`；不碰其他线路改动；探针实例测完停止，4004 保持在线。
