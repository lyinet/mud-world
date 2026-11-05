# 玩家门派面板（UI 接线）规格

> 契约：`docs/player-api.md` §13（已上线 `74b37ee`）。本规格只做前端接线与自检，不改后端动作语义。
> 目标：三种角色视角、13 个拒绝码中文、写动作在途禁用、真服务成功链、全量回归不退化。

## 1. 真实字段与缺口（以 `GET /api/player/state` 为准）

可直接使用的字段：

- 顶层 `sect` / `player.sect`：`in_sect`；`sect.id / faction_id / name / location / location_name / role / leader / leader_name / member_count / treasury.account / treasury.balance / disciples[] / limits / settled_today`。
- `disciples[]`：`id / name / skill / presence / at_sect / arrears`。
- `limits`：`disciple_cap / dues_daily / stipend_daily / arrears_expel`。
- 立派门槛所需：`player.wallet.money`（余额）、`growth.reputation.value`（声望原值）。
- 收徒门槛所需：`present_npcs[]` 的 `bond` / `trust`；“掌门技能 ≥0.4”用 `growth.skill.skills` 的最高值。
- 写动作响应：`details.sect`（**`{in_sect, sect}` 包装层**，真实快照在 `details.sect.sect`）与
  `details.settlement`（`dues / stipend / leavers[]`）。UI 必须解包后再读 `treasury.balance` / `member_count`。

缺口（**不造字段、不 mock**：统一显示「暂不可用」，并在页面注明缺哪个字段；回报里列端点+字段）：

1. `GET /api/player/state` → `sect.disciples[]` 没有 `affection` / `trust`。
   若 `player.relations[]`（`kind=master/disciple`）能匹配到该弟子，则显示真实好感/信任；
   匹配不到时该格显示「暂不可用（缺 disciples[].affection/trust）」。
2. `GET /api/player/state` → `sect.disciples[]` 没有逐人 `settled_today` / `duty`。
   逐人结算格显示「暂不可用（缺 disciples[].settled_today）」；门级 `sect.settled_today` 照常显示，不伪造逐人状态。
3. 后端没有“门派列表”读接口（`join_sect` 动作存在，`_faction` 接受 `sect:<id>` 或驻地 id）。
   未入派页的“加入已有门派”用输入框填门派 id 或驻地 id，默认预填当前 `location.id`；
   界面明示「暂不可用：后端未提供门派列表接口，需填写门派 id 或驻地 id」。
4. `join_sect` 没有“可加入门派”候选列表字段（无 `factions[]`）；同样不 mock。
5. `present_npcs[].bond` 用 `affection_between`，**不含 `sworn` 关系**；后端收徒裁决用所有
   active 关系的最大值（`_bond`）。真实 `recruit` 会把 sworn 写成好感 30 / 信任 45，但页面
   `bond` 仍可能是 0。UI 用同一次 `GET /api/player/state` 返回的 `player.relations[]`（真实字段）
   取最大值补齐，使页面门槛与后端裁决一致；两边都没有时才显示「暂不可用」。

### 1.1 门槛统一口径

每个门槛控件旁必须同时写三项：**当前值 / 需要值 / 差多少**（达标写「已达标」）。
覆盖：800 文、声望 20、好感 30、信任 30、收徒上限 3、金库保留金 100、单次提现上限 500。
后端未返回的当前值一律写「暂不可用（缺 <字段>）」，绝不按 0 冒充。

## 2. 界面结构

文件：`ui/player/index.html`、`ui/player/style.css`、`ui/player/app.js`。

- `#commerceTabs` 新增 tab：`data-tab="sect"`、`aria-controls="pane-sect"`、文案「门派」；tablist 的 `aria-label` 改为包含门派。
- 新增 `#pane-sect`（`role="tabpanel"`），内部容器：
  - `#sectHint`（顶部状态）
  - `#sectBody`（三种视角都由 JS 渲染，避免静态空壳）
  - `#sectResult`（成功/失败回显）
  - `#sectConfirmModal`：驱逐二次确认，正文必须写「驱逐后欠薪债务仍保留」。
- 不使用 `#pane-soon` 的“江湖”占位；门派是真实可用页。

## 3. 状态与渲染（`ui/player/app.js`）

### 3.1 状态

`state.sect = { account, name, doctrine, joinTarget, deposit, withdraw, result, resultBad, busy: {}, confirmTarget }`。
`applyState` 换账号时重置（与 `state.commerce/state.marriage` 同模式）。

### 3.2 只读投影

- `sectOf(st)`：优先 `st.sect.sect`，回退 `st.player.sect`；`in_sect=false` 或缺失返回 `null`。
- `sectRole(st)`：`leader` / `disciple` / ``。
- `sectBondOf(st, id)`：从 `st.player.relations` 匹配 `target===id`，取 `affection`、`trust` 的最大值。
- `sectReputation(st)`：`st.growth.reputation.value`，缺失返回 `null`（显示「声望未知」，不按 0 冒充）。
- `sectMaxSkill(st)`：`st.growth.skill.skills` 的最大值。

### 3.3 三种视角

**未入派 / 无门派数据**

- 若 `!state.st || !state.session || !st.player`：整页显示「登录并进入世界后才能查看门派」，不发任何写请求。
- 立派区：门派名（2-12 字）、宗旨（≤60 字，可空）；显示「需 800 文（现有 X 文，还差 Y 文）」与「需声望 20（现有 X，还差 Y）」。
  任一不足或输入名不合法时按钮 `disabled`，并写明差额；满足才可点「立派」。
- 加入区：输入框「门派 id 或驻地 id」默认 `st.location.id`；按钮「加入已有门派」；旁注「后端未提供门派列表，需填写 id 或驻地」。
- 写动作为 `found_sect {name, doctrine, at}` / `join_sect {target, dues:0}`。

**已入派非门主（disciple）**

- 只读展示：门派名、门主 `leader_name`、驻地、名册人数 `member_count`。
- 名册：弟子姓名、技能、是否在驻地、欠饷；自己的行高亮。
- 自己的俸禄：从名册找 `id === state.session.entityId` 的行；`arrears=0` → 「俸禄已结清」；否则「欠饷 X 文（约 N 天，日俸 10 文，满 30 文离派）」。
- 不渲染任何门主按钮；提示「只有门主可以收徒、派俸、驱逐与金库操作」。

**门主（leader）**

- 概况：门派名、门主名、名册 `member_count/limits.disciple_cap`、金库余额、`settled_today`。
- 名册：姓名、技能、好感/信任（来自 `player.relations`，缺失显示「暂不可用（缺 disciples[].affection/trust）」）、欠饷、逐人结算（缺失显示「暂不可用（缺 disciples[].settled_today）」）；门级 `settled_today` 单独显示。
- 名册人数/上限写成「当前 X / 上限 3 / 差 Y 或已满」。
- 收徒：列出 `present_npcs` 候选，每人旁写「好感 当前X / 需要30 / 差Y」「信任 当前X / 需要30 / 差Y」，外加「名册 当前X / 上限3 / 差Y」；未达标按钮禁用。
  掌门技能 <0.4 时整区禁用并写「掌门技能需 ≥0.4（现有 X）」。
  写动作 `recruit_disciple {target}`。
- 派俸禄：`settled_today===true` 或名册为空时禁用并写明原因（「今日已结算」/「门中暂无弟子」）。写动作 `pay_disciples {}`。
- 驱逐：每名弟子一个「驱逐」按钮 → 打开 `#sectConfirmModal`，正文含「欠薪债务仍保留」；确认后 `expel_disciple {target}`。
- 金库：
  - 存入：金额输入，上限为当前钱包余额；超限禁用并显示余额。
  - 取出：显示「可提上限 = min(500, 金库余额-100)」，并写「当前余额 X / 保留金 100 / 单次上限 500 / 最多可提 Y」；余额 <101 或输入超限时按钮禁用并写明 `withdraw_over_cap` / `treasury_reserve` 的中文原因。
  - 写动作 `sect_deposit {amount}` / `sect_withdraw {amount}`。

### 3.4 写动作通用规则

- 统一走 `sectAction(verb, args, label, busyKey)`：先 `requireAuthForWrite()`；`busyKey` 在途时按钮禁用、文案「处理中…」；成功/失败都刷新 `loadState`。
- 失败：`notifyError` + `reasonText(code)`；13 个拒绝码必须命中 `REASON_TEXT`，不得出现「未知错误」。
  执行前先用 `node --check` 与 `ui_check` 断言 `reasonText` 对 13 码均返回中文。
- 成功文案必须带真实数字（来自 `details.sect`，缺失则用刷新后的 `sectOf(state.st)`）：
  - 立派：`立派成功：{name} · 金库 {treasury.balance} 文 · 名册 {member_count} 人`
  - 收徒：`已收 {name} 为徒 · 名册 {member_count}/{disciple_cap} 人 · 金库 {treasury.balance} 文`
  - 派俸：`日结完成：收会费 {settlement.dues} 文，发俸禄 {settlement.stipend} 文，金库 {treasury.balance} 文` + 离派人数。
  - 存入/取出：`已存入/取出 X 文 · 金库 Y 文`。
- 只读模式（`data.status==='proposed' || data.readonly`）只提示“提案已提交，不落地”，不写成功数字。

### 3.5 空态与假数据

- 未进世界 / 匿名：`renderSectPane` 只清空容器并显示登录提示，不渲染名册、金库或门槛数字。
- `in_sect=false`：只渲染立派/加入区，不显示名册表头、金库 0 之类的伪数据。
- 每次渲染先 `innerHTML=''` 再填，避免上一位账号数据残留。

## 4. 样式

`ui/player/style.css`：

- 复用 `commerce-*` 基础样式；新增 `.sect-roster`、`.sect-row`、`.sect-row.self`、`.sect-meta`、`.sect-actions`、`.sect-modal-text`。
- 移动端（`max-width:768px`）所有门派按钮最小触达高度 44px；名册可横向滚动，不撑破右栏。
- 复用现有 `hidden`、`hint`、`bad`、`ok`、`pill` 语义类。

## 5. 自检

### 5.1 `tools/ui_check.py`

- 新增 `--only sect` 分组，`MockWorld` 增加 `/__check/sect/...` 造场（未入派、弟子、门主、匿名/无数据）。
- 用例（约 10 项）：
  1. 未入派显示 800 文/声望 20 与差额；余额不足时立派按钮禁用。
  2. 未入派不出现名册/金库假数据。
  3. 弟子视角显示门派名、门主、名册、自己的欠饷天数；不出现门主按钮。
  4. 门主视角名册含技能/好感/信任/欠饷/结算状态。
  5. 收徒候选未达标显示差多少且按钮禁用。
  6. 派俸在 `settled_today=true` 时禁用并回显「今日已结算」。
  7. 驱逐二次确认文案含「欠薪债务仍保留」。
  8. 取出上限 `min(500, balance-100)` 展示，超限禁用并显示中文上限原因。
  9. 13 个拒绝码经 `__XKX.reasonText` 均返回中文且不含「未知」。
  10. 移动端 375px：门派按钮高度 ≥44px。
- `results.must(...)` + `page.wait_for_function`，与现有 shopops 用例同模式。

### 5.2 真服务脚本 `tools/ui_check_real_sect.py`

仿 `tools/ui_check_real_shopops.py`：

- 复用 `free_port / start_api / start_proxy / stop_proc / ui_enter / wait_http` 与 `ui_check.Results/wait_app`。
- 新 run、新账号；`--auth off`、`XKX_AMBIENT=1`、`XKX_AMBIENT_SPEED=4`。
- 先挣钱到 ≥800 文（复用店铺脚本“卖初始物 → 买菜刀 → 押镖到 smithy”链路循环）。
- 真链：`found_sect` → 提升/选择达标 NPC 后 `recruit_disciple` → `pay_disciples`；每步校验 `details.sect` 真实数字。
- 收徒前用 `present_npcs` 的 `bond/trust` 选人；不足 30 时用真实 `talk` 提升（有上限），仍不足则如实记 `blocked_recruit_threshold` 并附真实数值与拒绝码，不得用 mock 顶替。
- 证据落 `reports/w5-sect/real-*.json|png`；进程退出即清理。

### 5.3 回归

- `python3 tools/ui_check.py --only sect`
- `python3 tools/ui_check.py`（全量，现有 155 项不得下降）
- `python3 tools/ui_check_real_sect.py`
- `node --check ui/player/app.js`

## 6. 文档与提交

- `docs/PLAYER-GUIDE.md`：新增门派面板操作说明（门槛、三种视角、13 拒绝码中文、金库上限）。
- `CHANGELOG.md` 顶部新增 W5 门派面板条目。
- 显式 pathspec 提交，只含：`ui/player/index.html`、`ui/player/style.css`、`ui/player/app.js`、`tools/ui_check.py`、`tools/ui_check_real_sect.py`、`reports/w5-sect/*`、`docs/PLAYER-GUIDE.md`、`CHANGELOG.md`、`specs/player-sect-ui/*`；不 stage 其他 session 的 `src/**`。

## 7. 验收

1. 三种角色视角截图/断言通过。
2. 13 个拒绝码全部中文；写动作在途显示「处理中…」。
3. 真服务脚本至少立派 → 收徒 → 派俸成功；若后端拒绝，报告真实码与数值。
4. `--only sect` 全绿，全量回归 ≥155 全绿。
5. 分三次回报：三种角色视角、真服务脚本、全量回归。
