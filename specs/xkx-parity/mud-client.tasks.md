# xkx MUD 客户端界面 · 实施任务

依赖 spec：`specs/xkx-parity/mud-client.spec.md`

## T1 后端只读字段（session.py）

- [x] `_skills_xkx_view(ent)`：`skill_levels/skill_xp` → `[{name,level,xp}]`
- [x] `_weight_view(ent)`：行囊 + 已装备按 `content.items[item].weight` 求和
- [x] `_exploration_view(pid, loc_id)`：事件日志 `set_location` 重建已访问集合（惰性一次扫描）；
      区域只读引擎 `region`，缺失时 `area/area_total/area_explored=null` + `area_reason`，
      禁止按 id 前缀推断
- [x] `player_state.player` 增补 `vitals/combat_exp/attrs/attrs_detail/attrs_reason/weight/
      skills_engine/skills_status/exploration`
- [x] `player.equipment.actions` 增补 `equip/unequip/reason`（按 registry 是否注册）
- [x] `ground` 行增补中文 `name`
- [x] `xkx_help_catalog()` + `_xkx_shell_instance()` 复用壳实例

验证：`python3 -c` 起 WorldSession 读 state，逐字段对照 entity；pytest T6 用例。

## T2 help 数据与壳内命令（xkx_shell.py）

- [x] `__init__` 读取 `content_root/xkx_help/commands.yaml`、`topics.yaml`、`manifest.json`
- [x] 建索引：命令名+别名、主题 id+标题、系统分组、状态计数
- [x] `_cmd_help(rest, state, raw, request_id)` 实现总览 / 命令分类 / 命令详情 / 主题列表 / 主题详情
- [x] 状态文案：`已实现 / 待实现（原因：blocker）/ 不做（原因：blocker）`；
      另起一行「本文字壳：已接入口 / 未接入口」
- [x] `管理` 主题不贴正文；`未开放` 主题正文前加状态
- [x] `_catalog()` 返回 commands/topics/counts/shell_verbs
- [x] 未知命令统一走 `_unknown_lines(raw)`：help 已知待实现/不做 → 直接给原因；
      否则「你没听懂「X」。你是想打「Y」吗？」
- [x] `execute` 的 `help` 分支改为带参数调用

验证：pytest T6 的 help/unknown 用例。

## T3 GET /api/xkx/help（server.py）

- [x] 常量 `_XKX_HELP`，`_do_GET` 匿名直接返回 `session.xkx_help_catalog()`
- [x] 确认不含玩家私有字段

验证：`curl http://127.0.0.1:8041/api/xkx/help | jq '.counts'` 为 153/32/40/51/62。

## T4 前端面板（index.html / style.css / app.js）

- [x] index.html：新增人物、技能、装备、任务、行囊、地面面板；舆图加 `#map-meta`；
      `#complete`；顶栏 `#pill-hp`
- [x] app.js：启动与命令后 `GET /api/player/state` → `renderState`
- [x] 人物：气血条用 `hp_pct`；无值「未开放（原因）」；内力恒未开放；经验/属性/金钱/负重
- [x] 技能：engine → detail 回退；不可用显示原因不消失
- [x] 装备：9 槽全列；穿脱常驻「暂不可用（原因）」，不渲染按钮
- [x] 任务：`active_quests_detail/active_quests`；无任务给文案，字段缺失给原因，不做假按钮
- [x] 行囊点击 `sell`；地面点击 `get`；空地面有文案
- [x] 舆图标题：有 `region` 显示本区计数；无 `region` 显示「暂不可用（原因）」+ 全图计数；
      地图只用真实 `view.exits`
- [x] state 失败时面板显示「状态暂不可用」，不隐藏
- [x] style.css 新样式，保持移动端单列

验证：浏览器人工+Playwright 截图。

## T5 命令补全

- [x] 启动 `GET /api/xkx/help` 缓存目录（静态，不缓存游戏状态）
- [x] 输入前缀匹配命令/别名，最多 8 条，shell 优先，标注状态与原因
- [x] Tab 最长公共前缀 / 唯一补全；↑↓ 列表导航；Enter 提交；点击建议填入
- [x] 未知命令后端提示，前端只渲染日志

验证：`lo` 出现 look/loot 建议；`zzz` 回车出现「没听懂」。

## T6 测试（tests/test_xkx_api.py）

- [x] vitals/attrs/exp/weight/neili 字段与来源一致
- [x] skills 二选一（数据或原因）
- [x] exploration 初始≥1、移动后增加、总数 195；无 region 时 area=null + area_reason
- [x] 任务字段存在且不伪造
- [x] help catalog 153/32/40/51/62；`look.shell=true`、`baitan.shell=false`
- [x] `help 命令 战斗` 有「待实现」+原因；主题正文；管理主题不贴正文
- [x] 未知命令「没听懂」+建议；`baitan` 不写成可用

验证：`pytest tests/test_xkx_api.py -q` 全绿；再跑 P3 套件。

## T7 真实世界验收与提交

- [x] kill 8011/8041 旧进程；`./scripts/start_world.sh --world xkx --reset`
- [x] Playwright：面板、移动后探索计数、help、help 命令、补全、未知命令各一张截图
- [x] 原始 state JSON 存 `/tmp/xkx-state.json`，截图复制到 `/Users/lyinet/projects/XKX/`
- [x] `git fetch` + 快进 `xkx-parity`；显式 `git add` 变更文件；中文提交；推 `origin xkx-parity`

## 边界

- 不改 `src/wsim/sim/**`、`src/wsim/actions/**`、`src/wsim/world/**`、`tools/**`、`content/**`
- 不 force push；不动他人未提交改动
- 不把待实现命令写成可用；不新增前端游戏状态
