# 玩家前端指南（W4：引导 / 诊断 / 移动端 / 鉴权）

适用页面：`ui/player/`（纯 JS，无前端框架）。本指南只描述前端行为；服务端契约仍以 `src/wsim/api/**` 为准。

## 1. 新玩家 4 步引导

首次进入世界（该账号在 `localStorage` 的 `ws_player_guide_v1` 中没有 `skipped` / `completed` 记录）时自动出现，一次只高亮一个元素：

| 步骤 | 高亮元素 | 一句话「为什么」 | 何时进入下一步 |
| --- | --- | --- | --- |
| ① | `#todayNext`（今天能做什么 → 先做这个） | 它会指出今天最值得做的一件事，省得在 12 行信息里挑。 | 点「下一步」 |
| ② | 第一条可接委托的「去接」按钮 | 委托是前期最稳的收入；接单后任务会留在任务板。 | 接单成功自动进入③；或点「我已接单，下一步」 |
| ③ | 委托人/目标所在地的出口按钮；已在该地时直接高亮「交付」 | 点出口逐段走过去，到了才能交付。 | 移动成功且到达目标自动进入④；或点「我已到达，下一步」 |
| ④ | 进行中委托的「交付」按钮 | 交付后报酬从委托人钱包转给你；留意顶部「钱」的数值变化。 | 交付成功自动完成，并提示资金变化 |

- 可随时点引导卡右上角「跳过」。
- 引导卡与高亮框都设 `pointer-events: none`（卡片本身可点），卡片按目标包围盒自动放上方/下方，**不会盖住被高亮的元素**。
- 持久化在客户端 `localStorage`：
  - key：`ws_player_guide_v1`
  - 结构：`{ "<account_id>": { "status": "active" | "skipped" | "completed", "step": 1-4, "updated": 时间戳 } }`
  - `skipped` / `completed` 的账号刷新、重进都不会再出现。

## 2. 失败可诊断

- 所有 `/api/player/action`、`/api/player/enter` 的非 2xx 响应都读 `reason` / `reject_reason`（兼容 `error`），先查前端中文映射，再回退服务端 `reason_text/message`。
- 未知原因码兜底：`操作没有完成：<code>（服务端没有给出中文说明，请刷新后再试）。`
- 错误提示在顶部居中，`pointer-events: none`，5.2 秒自动消失；右下角「最近错误 N」可展开复查最近 3 条（含原因码与时间）。
- 完整映射表见第 6 节。

## 3. 移动端（≤768px，重点 375×812）

- 单列排序：`今天能做什么 + 任务板` → `当前位置/出口/NPC` → `操作记录/对话`。
- 侧栏每组（今天、成长线、任务板、位置、NPC）都有 44px 折叠按钮；移动端默认收起「成长线」，其余展开。被引导高亮的组会自动展开。
- 所有可见按钮、输入框、出口、NPC 触控目标 `min-height: 44px`。
- 实测 375×812 主流程 4 步：目标都在视口内、可被 `elementFromPoint` 命中、无横向滚动；`reports/w4-ui/mobile-summary.json` 与 `mobile-step*.png` 为证。

## 4. 键盘可达

- 主流程按钮均为原生 `button`，`Tab` 可依次到达「去接」→「药铺出口」→「交付」，`Enter` 触发。
- `:focus-visible` 为 3px 金色描边 + 2px offset；入口身份卡使用 `:focus-within`。
- 实测 28 次 Tab 完成接单→移动→交付；焦点序列写入 `reports/w4-ui/keyboard.txt`。

## 5. 登录 / 注册与令牌（W1 已对齐）

> **契约状态：已按 W1 实际实现对齐。** 路径是 `/api/auth/*`（不是 `/auth/*`）；以下样例与 `src/wsim/api/auth.py` 一致。

### 5.1 端点与真实样例

| 用途 | 请求 | 成功响应 |
| --- | --- | --- |
| 注册 | `POST /api/auth/register` `{username, password}` | `{"ok":true,"account_id":"u_alice","username":"u_alice","created":true}`（**不返回令牌**） |
| 登录 | `POST /api/auth/login` `{username, password}` | `{"ok":true,"account_id":"…","username":"…","session_id":"s:…","token":"…","token_type":"Bearer","expires_at":1790000000,"expires_in":86400}` |
| 续期 | `POST /api/auth/refresh`，头 `Authorization: Bearer <当前 token>`，无请求体 | `{"ok":true,"account_id":"…","session_id":"s:…","token":"<新>","token_type":"Bearer","expires_at":…,"expires_in":86400,"rotated":true}` |
| 注销 | `POST /api/auth/logout`，Bearer | `{"ok":true,"logged_out":true,"session_id":"s:…"}` |
| 当前身份 | `GET /api/auth/me`，Bearer | `{"ok":true,"account_id":"…","username":"…","session_id":"s:…","status":"active"}` |
| 写操作 | `POST /api/player/enter`、`POST /api/player/action`，Bearer | 进入世界、移动、接单、交付、对话、成长动作 |

统一错误体：`{"ok":false,"code":"…","reject_reason":"…","reason_text":"…","tick":n}`，其中 `code` 与 `reject_reason` 同值。前端优先读 `code`，再回退 `reject_reason`/`reason_text`。

注册成功体没有令牌，因此前端在注册 200 后**自动用同一组用户名/口令调用登录**换令牌；不会把注册响应当作登录态。

### 5.2 令牌存储、轮换与单飞

- localStorage key：`ws_auth_v1`，仅 `{access_token, expires_at, username, session_id}`；**不存口令**，也不存 refresh token（真实契约没有独立 refresh token）。
- 启动时若已有令牌：带 Bearer 请求读端点确认可用性，再以 `POST /api/auth/refresh` 探针判定模式并完成一次轮换；401/失效（`auth_missing_token`/`auth_invalid_token`/`auth_expired_token`）→ 清掉本地令牌，回到「登录/注册」态，不保留假登录。
- **refresh 是轮换式**：服务端返回新 token 后旧 token 立即失效。前端在刷新成功后**原子替换** `state.auth.access` 并立即 `persistAuth()`，刷新期间和刷新后都不再使用旧 token。
- **单飞刷新**：`state.auth.refreshing` 保存同一个 refresh Promise。多个并发写请求同时 401 时，只有第一个发起 refresh，其余等待同一结果；refresh 成功后统一用新 token 各重放一次。若 401 返回时本地令牌已被别的请求换新，则直接重放，不再发第二次 refresh。重放只允许一次，仍 401 则进入重新登录流程。
- 写操作 401：自动单飞 refresh + 重放一次；仍失败则清令牌、显示「需要重新登录」横幅/顶部按钮/错误历史，**保留当前世界状态与已渲染面板**；并发失败只提示一次。

### 5.3 legacy 匿名模式

不读 `deploy/service.yaml`、不硬编码。启动时探测 `POST /api/auth/refresh`：

- `404/405/501` → legacy，无需登录；顶部显示「匿名模式」，进入/写操作照常。
- `503` 且 `code=auth_disabled`（真实 legacy 服务没有账号库）→ 同样视为 legacy，匿名可玩。
- `200` / `401`（`auth_missing_token` 等）→ 鉴权模式，显示「登录/注册」。
- 网络不可达时保守放行：有令牌按鉴权模式、无令牌按匿名模式；写操作若返回 401 再走上面的重新登录流程，不能强行拦住旧匿名玩法。

## 6. 原因码 → 中文文案（完整映射）

| 原因码 | 中文文案 |
| --- | --- |
| `auth_required` | 需要登录后才能执行写操作；请先登录/注册。 |
| `auth_bad_username` | 用户名需为 3-32 位小写字母、数字、下划线或连字符。 |
| `auth_bad_password` | 口令长度需为 6-128 位。 |
| `auth_username_taken` | 该用户名已被注册；换一个用户名再试。 |
| `auth_invalid_credentials` | 用户名或口令不正确；请检查后重试。 |
| `auth_missing_token` | 缺少登录令牌；请重新登录。 |
| `auth_invalid_token` | 登录令牌无效或已注销；请重新登录。 |
| `auth_expired_token` | 登录已过期；请重新登录。 |
| `auth_account_mismatch` | 请求账号与令牌账号不一致；请用令牌对应的账号操作。 |
| `auth_disabled` | 当前为 legacy 匿名模式，鉴权接口不可用；可匿名游玩。 |
| `bad_json` | 请求体不是合法 JSON；请刷新页面后重试。 |
| `invalid_token` | 登录凭证无效；请重新登录。 |
| `token_expired` | 登录已过期；请重新登录。 |
| `bad_credentials` | 用户名或口令不正确；请检查后重试。 |
| `username_taken` | 这个用户名已被占用；换一个用户名再注册。 |
| `weak_password` | 口令太短或格式不合要求；请换一个更稳妥的口令。 |
| `bad_username` | 用户名格式不合法；请用 3-32 位字母、数字、下划线或连字符。 |
| `auth_unavailable` | 登录服务暂时不可用；可先匿名浏览，稍后再试。 |
| `missing_account` | 缺少账号；请先创建账号再进入世界。 |
| `bad_account` | 账号格式不合法（3-32 位小写字母、数字、下划线或连字符）。 |
| `unknown_action` | 未知操作；请刷新页面后重试。 |
| `forbidden_verb` | 该操作在本阶段不开放（伤害/抢掠/强迫婚配等不可逆动作被禁用）。 |
| `player_not_entered` | 你还没有进入世界；请先进入世界。 |
| `player_offline` | 你已离线；请重新进入世界后再操作。 |
| `already_offline` | 你已经是离线状态。 |
| `already_online` | 你已经在线，无需重复进入。 |
| `identity_locked` | 身份进入后不可更改；请用原身份重新登录。 |
| `bad_archetype` | 身份必须是游侠、商人或捕快。 |
| `bad_display_name` | 角色名需 2-12 字，且不能与现有角色重名。 |
| `bad_location` | 地点不存在。 |
| `unknown_location` | 地点不存在；请从当前地点可用的出口中选择。 |
| `not_adjacent` | 目标地点与当前位置不相邻；请沿道路逐段移动。 |
| `insufficient_stamina` | 体力不足；请先休息或进食。 |
| `unknown_target` | 目标人物不存在。 |
| `not_together` | 对方不在你当前所在的地点；先移动到对方所在处。 |
| `target_offline` | 对方不在线，不能进行该操作。 |
| `player_consent_required` | 该操作需要对方本人同意；本阶段关闭 NPC 对玩家的婚配与招揽。 |
| `bad_message` | 消息不能为空且不超过 200 字。 |
| `heartbeat_too_soon` | 心跳过于频繁；请稍后再试。 |
| `stale_tick` | 你的请求使用了过期时间点；请刷新状态后重试。 |
| `unknown_verb` | 动作未注册；请刷新页面后重试。 |
| `quest_not_found` | 这条委托已不在任务板上；请刷新任务板换一条。 |
| `quest_not_open` | 任务已被接走或已结束；请换一条开放中的委托。 |
| `giver_missing` | 任务发布人已不在世界；这条委托无法继续。 |
| `not_at_giver` | 你不在委托人所在的地点；先走到委托人那里。 |
| `busy_on_escort` | 你正在押镖，不能同时接单；先完成押镖。 |
| `too_many_quests` | 你手上已有 2 个进行中任务；先交付其中一个，再接新单。 |
| `quest_not_accepted_by_actor` | 这个任务不是由你接下的，不能由你交付。 |
| `quest_expired` | 这条委托已超过截止时间；去任务板换一条未过期的。 |
| `reputation_too_low` | 声望还不够接这一单；先做小委托或经营人情，达到门槛再来。 |
| `not_at_target` | 还没到任务目标地点或委托人处；先移动过去再交付。 |
| `quest_item_missing` | 采购类委托需要你持有目标物品；先取得物品再交付。 |
| `item_not_held` | 你身上没有这条委托要求的物品；先取得物品再交付。 |
| `trade_unfair` | 交易价值明显不对等；对方拒绝了这笔买卖。 |
| `service_item` | 服务类物品不能用于玩家交易。 |
| `offer_not_held` | 你给出的物品数量不足。 |
| `want_not_held` | 对方没有你想要的物品。 |
| `insufficient_money` | 你的钱不够。 |
| `target_insufficient_money` | 对方的钱不够支付这笔差额。 |
| `hostile` | 对方对你敌对，拒绝交易。 |
| `role_not_allowed` | 你的身份不允许该操作。 |
| `player_cap_reached` | 本区域玩家席位已满，暂时无法创建新账号。 |
| `endowment_already_granted` | 该账号已经领取过初始资产，不能重复领取。 |
| `endowment_cap_reached` | 本区域初始资产发放总额已达上限；请联系运维。 |
| `account_missing` | 玩家钱包账户不存在，已被回滚。 |
| `effect_invalid` | 操作违反世界规则，已被回滚。 |
| `bad_args` | 参数不完整或格式错误。 |
| `bad_arg:money_delta` | 金额参数必须是整数。 |
| `intent_not_found` | 没有找到这个请求；请刷新后重试。 |
| `readonly_mode` | 只读模式不支持该动作；请用可写模式启动。 |

未知码：优先显示服务端 `reason_text/message`；都没有时显示上一节的兜底文案。

## 7. 自动化验收

```bash
python3 tools/ui_check.py                 # 全部：原因码/桌面/移动/键盘/回归/鉴权
python3 tools/ui_check.py --only desktop  # 只跑某一组
python3 tools/ui_check.py --serve         # 起 mock 页面，手动看；--auth 启动鉴权模式
```

- mock 服务只改内存，不写 `runs/`、不启动世界 tick，避免与其它会话的确定性基线冲突。
- 证据目录：`reports/w4-ui/`（`checks.json`、桌面/移动/鉴权截图、`keyboard.txt`、`mobile-summary.json`）。

## 8. 未覆盖 / 已知限制

- W1 契约已对齐；`/api/auth/*` 的真实响应样例来自 W1 收口说明与 `src/wsim/api/auth.py`，未在真实服务上做端到端联调（本轮仍用 mock 服务验收）。
- 不做第三方登录；不做口令找回。
- 引导③不做路径规划：若委托人不在相邻地点，只高亮当前可用出口之一，需玩家继续逐段移动；到达后自动进入④。
- 只读模式（`--readonly`）下交付会返回提案，不会真正产生资金变化，引导④进度会停在交付前，可用「跳过」结束。
- 桌面 1280×900、移动 375×812 已实测；其它尺寸只保证布局断点逻辑一致，未逐像素验收。
