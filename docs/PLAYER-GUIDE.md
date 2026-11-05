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
- 未知原因码兜底：`操作没有完成；服务端没有给出中文说明，请刷新后再试。`（界面不显示原始 code）。
- 错误提示在顶部居中，`pointer-events: none`，5.2 秒自动消失；右下角「最近错误 N」可展开复查最近 3 条（中文文案与时间）。
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
| `not_enough_money` | 你的钱不够（买入价含交易税）。 |
| `item_not_available` | 这件物品不在当前内容包里，买不到。 |
| `item_missing` | 你背包里没有足够的这件物品。 |
| `not_here` | 你不在这个交易地点。 |
| `shop_closed` | 这里没有店铺，不能买卖。 |
| `not_a_merchant` | 这里的店铺不做这件货的买卖。 |
| `unknown_item` | 这件物品在世界里不存在。 |
| `no_buyer_cash` | 本地店铺现金不够，暂时收不下这批货。 |
| `wrong_location` | 你不在这家店铺所在的地点。 |
| `bad_arg:qty` | 数量必须是 1-20 的整数。 |
| `bad_arg:item` | 物品参数不合法。 |
| `bad_arg:from` / `bad_arg:to` | 交易地点参数不合法。 |
| `not_at_origin` | 你不在镖单的起点。 |
| `route_not_adjacent` | 押镖路线不连续；只能沿相邻地点逐段走。 |
| `already_on_escort` | 你正在押镖，不能同时接第二单。 |
| `cargo_too_low` / `cargo_too_high` | 货值低于最低承运要求 / 超过最高承运额度。 |
| `fee_too_low` / `fee_too_high` | 镖银低于这条线路的最低标准 / 超过最高标准。 |
| `no_escort_agency` | 起点没有镖局，不能接镖。 |
| `agency_insufficient_cash` | 镖局现金不足以支付这单镖银。 |
| `bad_arg:route` | 路线格式错误；请从当前地点沿相邻出口选择终点。 |
| `bad_arg:cargo_qty` | 货物数量必须是 1-100 的整数。 |
| `bad_arg:cargo_value` / `bad_arg:fee` | 货值 / 镖银必须是整数。 |
| `treasury_guard` | 商会护盘期间暂不可开店。 |
| `treasury_buffer` | 区域金库保底线不足，开店被暂缓；过一会儿再试。 |
| `bad_shop_type` | 店铺档位不合法；只能选摊、店面或分号。 |
| `already_owns_shop` | 你已经有店铺了，不能再开第二家。 |
| `no_landlord` | 这个地点没有可签约的铺面。 |
| `no_shop` | 你还没有店铺；先开一家再来经营。 |
| `not_shop_owner` | 这家店铺不是你的，不能经营。 |
| `not_player_shop_hire` | 只有玩家店铺可以雇工。 |
| `target_not_found` | 要雇的人不在这个世界。 |
| `self_recruit` | 不能雇自己。 |
| `target_not_at_shop` | 对方不在你的店铺所在地点。 |
| `already_hired` | 对方已经在你的店里做工。 |
| `target_owns_shop` | 对方自己开着店，不会来做雇工。 |
| `shop_full` | 店铺雇工已满；升级铺面或先解雇再雇。 |
| `unknown_position` | 这个岗位不在市价表里；请从可选岗位中选择。 |
| `skill_too_low` | 对方手艺还不够（技能低于 0.15）。 |
| `relation_too_low` | 好感不足 6.0；先多交谈、经营人情再来雇。 |
| `wage_too_low` | 日薪低于市价的 0.8 倍，对方不会接受。 |
| `wage_too_high` | 日薪高于市价的 1.5 倍，对方觉得不合理。 |
| `price_out_of_range` | 定价只能在基准价的 ±20% 以内；超出范围没人敢应价。 |
| `insufficient_shop_cash` | 店铺现金不足，不能提这么多；先留着进货或等日结回款。 |
| `target_not_employed` | 这个人不在你的店里做工，无法辞退。 |
| `bad_arg:qty` | 进货数量必须是 1-20 的整数。 |
| `bad_arg:price` | 价格必须是整数。 |
| `bad_arg:amount` | 提现金额必须是正整数。 |
| `already_married` | 你已经成婚了，不能再结一次。 |
| `not_married` | 你还没有婚约，无法进行这个操作。 |
| `bond_too_low` | 感情还没到谈婚论嫁的地步；先多相处、经营人情。 |
| `trust_too_low` | 信任不足；先建立更深的交情。 |
| `gift_missing` | 提亲需要带一件像样的礼物。 |
| `self_propose` | 不能向自己提亲。 |
| `target_in_couple` | 对方已有婚约或配偶，不能再谈。 |
| `spouse_not_here` | 你的配偶不在当前地点；先走到对方那里。 |
| `target_not_npc` / `target_not_player` | 对方不是可婚配的人物 / 该操作只对玩家角色有效。 |
| `spouse_not_alive` / `spouse_offline` | 配偶已不在人世 / 配偶不在线，暂时不能互动。 |
| `unknown_actor` | 操作发起人不在世界里；请刷新后重试。 |
| `bad_arg:gift_money` | 聘礼必须是整数。 |
| `bad_arg:gift_qty` | 信物数量必须是 1-20 的整数。 |
| `no_payer_funds` | 付款方资金不足，这单暂时接不了。 |
| `cannot_rest_while_escorting` | 押镖途中不能休息；先完成这趟镖。 |
| `cannot_rest_now` | 现在不能休息；请稍后再试。 |
| `bad_arg:hours` | 休息时长必须是 1-8 小时的整数。 |

未知码：优先显示服务端 `reason_text/message`；都没有时显示上一节的兜底文案。

## 7. 商业与经营面板（W4）

右栏「商业与经营」区块（`#commerceBlock`，顶栏「商业」按钮可直达），七页签：

| 页签 | 入口 | 动作与真实请求 |
| --- | --- | --- |
| 商店 `shop` | 当前地点有铺面时列出可买/可卖 | `buy {item,qty}`、`sell {item,qty}`、`consume {item,qty:1}` |
| 经营 `business` | 摊 / 店面 / 分号三档 | `restock {item,qty}`、`set_price {item,price}`、`dismiss {target}`、`withdraw {amount}`、`shop_report {}`；`open_shop {kind}` 开店 |
| 雇人 `hire` | 当前地点在场 NPC | `recruit {target,position,wage}`；日薪默认岗位市价 |
| 押镖 `escort` | 所在地有镖局才可用 | `escort {route,[cargo_item],cargo_qty,cargo_value,fee}` |
| 婚姻 `marriage` | 当前地点有可求婚 NPC 时列出候选 | `marriage_propose {target,gift_money,gift_item,gift_qty}`；配偶栏「和离」发 `marriage_end {}` |
| 门派 `sect` | 未入派显示立派 / 加入；已入派按门主、弟子两种视角 | `found_sect {name,doctrine}`、`join_sect {target}`、`recruit_disciple {target}`、`pay_disciples {}`、`expel_disciple {target}`、`sect_deposit {amount}`、`sect_withdraw {amount}` |

- **可买**：名称、单价、含税总价、库存、`buy_limit`；数量用原生数字输入（1-20，受 `buy_limit`/库存/现金约束），每行「买」按钮。
- **可卖**：名称、`sell_price`、持有数量；数量上限为持有数，每行「卖」按钮。
- **现吃**：`consumeBtn` 买一份当场吃下，结果行显示 `体力 82 → 100（+18）· 钱包 294 → 291 文（-3）`。
- **押镖**：终点来自服务端 `exits`（相邻校验）；货物只列 `wallet.items` 中真实持有且数量 >0 的；镖费默认 180，实时给出预计到手（服务端口径 `fee - int(fee × 30%)`）与到货 tick（`tick + 144`）。
- **雇人**：行内显示好感、技能、受雇状态与本地前置判断；服务端拒绝码全部映射中文（第 6 节），本地资金/容量不足时按钮禁用并给出原因。
- **刷新**：主状态 8 秒轮询；输入框聚焦或有未保存数字时暂缓重建列表，失焦后补渲染，不打断正在输入的表单。
- **触控与键盘**：页签、行按钮、数字/下拉输入 `min-height: 44px`；页签支持 `←/→/Home/End`，行输入可用 Tab 到达并回车触发。
- 证据：`reports/w4-commerce/`（`real-commerce.json` 含每个动作的真实请求/响应与 UI 快照，`real-*.png` 为桌面/移动截图）。

### 7.1 店铺经营：进货 / 定价 / 辞退 / 提现 / 7 日账本

「经营」页签在自有店铺下方依次给出五个区：

- **进货**：每件货一行，填数量（1-20），行尾即时显示「合计 N 文」；点「进货」后提示
  「进货 炊饼×6，花费 18 文，库存 4 → 10」。进货从店铺现钱扣，库存增加。
  任一常规 SKU 低于 3 件时行内标红「库存不足（低于 3 件）」，售罄标「已售罄 · 优先补货」，
  并出现「补到 10/15/20 件（按摊 / 店面 / 分号）」按钮：只按档位建议量预填数量，仍需再点
  「进货」确认，不会替玩家直接下单。
- **定价**：每件货一行，显示当前价与基准价；数字框与滑块都受基准价 ±20% 约束。输入时直接显示
  「当前基准价 X 文，你的价 Y 文，比基准高/低 Z%」，并提示「过高可能没人买」；超出范围时
  行尾标红「超出 ±20%」，按钮不发请求。
- **辞退**：店内雇工每行一个「辞退」按钮，点开二次确认，写明「欠薪仍计」。确认后提示
  「已辞退 X，欠薪 N 文仍计」；欠薪不会因辞退自动消失。
- **提现**：填金额（正整数），下方显示当前店现；先从店铺公账扣，再进你的钱包。成功提示
  「提现 20 文，店现 100 → 80」。
- **7 日账本**：表格列为世界日 / 营收 / 进货 / 工资 / 税 / 净利 / 笔数 / 说明；净利正数绿色、
  负数红色，负行按该行数值给出简短原因（当日无客流 / 工资高于营收 / 进货占去营收 / 入不敷出）。
  若某日 `stockout: true`，说明列用中文直说「缺货导致零成交（炊饼/阳春面）」，不再笼统写
  「当日无客流」；账本上方同时提示最近 7 个日结里有几天缺货。表格底部有一行「7 日合计」。
  开店不足一日或日结未到时显示「尚无日结记录，世界日结束后来看」；未开店显示
  「你还没有店铺；开张后才有日结账本」；不是店主显示「你不是店主，看不到这家店的账本」，
  三种空态都不显示示例行或假库存。

无店或不是店主时，四个操作区与辞退按钮全部禁用并给出原因；动作在途时按钮转圈（文案
「处理中…」）且不可重复点击。375×812 下账本表格可横向滚动，页面不横向溢出；新增控件均 ≥44px。
真服务若尚未实现这些动作，验收脚本会显式标记 `blocked_backend_not_ready`，不会用 mock 冒充真服务证据。

### 7.2 门派：立派 / 收徒 / 派俸 / 驱逐 / 金库

门派页签按三种身份渲染，数据只来自 `GET /api/player/state` 的 `player.sect`（写动作响应的
`details.sect` 同源）：

| 身份 | 页面内容 | 可写动作 |
| --- | --- | --- |
| 未入派 | 立派（门派名、宗旨、800 文与声望 20 门槛）；加入（门派 id 输入） | `found_sect`、`join_sect` |
| 弟子（非门主） | 门派名、门主、名册、自己的俸禄状态与欠饷天数 | 无（显示「只有门主可以…」） |
| 门主 | 名册（技能/好感/信任/欠饷/逐人结算状态）、收徒门槛、派俸、驱逐、金库 | `recruit_disciple`、`pay_disciples`、`expel_disciple`、`sect_deposit`、`sect_withdraw` |

- **门槛三项**：每个门槛控件旁直接写「当前值 / 需要值 / 差多少」——立派 800 文与声望 20、
  收徒好感 30 / 信任 30 / 掌门技能 0.4、名册上限 3、金库保留金 100 与单次取出上限 500、
  可提上限 = min(500, 金库余额 − 100)。不足时按钮禁用，但差额与原因始终可见。
- **在途禁用**：写请求发出后按钮变「处理中…」并禁用，防重复提交；成功后再按服务端快照重绘。
- **成功文案带真实数字**：立派「金库 600 文 · 名册 1 人」；收徒「金库 X 文 · 名册 N 人」；
  派俸「收会费 A 文，发俸禄 B 文，金库 C 文，名册 N 人」，A/B/C 取自 `details.settlement`
  与 `details.sect`，不写死。当日已结算后派俸按钮禁用并显示「今日已结算」。
- **驱逐**：二次确认弹层写明「欠薪债务仍保留」；确认才发 `expel_disciple`。
- **拒绝码中文**：`no_sect / insufficient_funds / insufficient_rep / already_in_sect /
  not_sect_leader / favor_too_low / trust_too_low / sect_full / disciple_not_found /
  withdraw_over_cap / treasury_reserve / insufficient_treasury / already_settled_today`
  全部映射为中文，未知码也不会显示「未知错误」。
- **未登录 / 匿名**：门派区只显示「登录并进入世界后查看门派。」，不渲染空壳门派、名册或金库。

## 8. 婚姻：如何求婚 / 如何和离

婚姻入口在右栏「商业与经营」的「婚姻」页签（顶栏「商业」按钮可直达）。规则以服务端 `src/wsim/actions/marriage.py` 为准，前端只做展示与提交。

### 8.1 求婚

1. 先和对方同处一地。候选列表只列**当前地点在场**的 NPC，含「好感 / 信任 / 同地点 / 婚姻状态」；不在场的 NPC 不会出现。
2. 把好感和信任攒够：**接受**需要 `好感 ≥ 60 且 信任 ≥ 30`；只到 `好感 ≥ 45 或 信任 ≥ 20` 时对方会「再处处看」（不扣聘礼/信物，关系不推进）；更低则为婉拒。
   - 好感靠 `talk` 累积，服务端每人每 tick 最多 +2、每人每世界日最多 +8；新世界的初始好感为 0，所以要从头攒到 60 需要跨约 8 个世界日。
   - 信任在首次交谈后从 40 起算，一般不需要额外经营；若因事件下降，同样用交谈补。
3. 在列表里选一位 NPC，填**聘礼金额**（整数，不能超过钱包现金）与**信物**（下拉只列背包里真实持有、数量足够的物品；数量 1-20）。不选信物也可以，只要不填数量。
4. 点「提亲」。裁决结果分三种：
   - **接受**：显示「与 X 成婚，tick N」，同时从钱包扣掉聘礼、从背包扣掉信物，配偶常驻区立即出现；
   - **再处处看**：显示「对方说：再处处看」，聘礼与信物**不扣**；
   - **婉拒**：显示中文原因（好感/信任不足、对方已有婚约、双方不同地点、已婚后重复提亲、聘礼或信物不足等），**不扣**聘礼与信物。

### 8.2 配偶常驻区

成婚后右栏固定出现「配偶」块（即使切到别的页签也不消失）：

- 第一行：`配偶：姓名 · 成婚 tick N`。
- 第二行：配偶当日陪伴情况。接口有 `last_daily_tick / daily_note` 时直接显示当日记录；没有时以「配偶是否与你同地点（`nearby` 近似）」判断，并在块底注明「按 nearby 同地点近似」。
- 对话对象是配偶时，NPC 列表、对话目标、对话结果与回复都会标「（配偶）」并加高亮；配偶回复的抬头是「配偶回应」，不会和普通「世界回应」混在一起。

### 8.3 和离

1. 点配偶块的「和离」按钮，弹出二次确认框，写明：和离后双方恢复单身；**聘礼不退还**，共同财产不分割。
2. 「取消」不做任何事；「确认和离」提交 `marriage_end {}`，成功后回到单身态、配偶块隐藏。
3. 和离要求配偶与你同处一地；不在同一地点时服务端返回 `spouse_not_here`（中文提示「你的配偶不在当前地点」），走到对方所在地再试。

## 9. 自动化验收

```bash
python3 tools/ui_check.py                 # 全部：原因码/桌面/移动/键盘/回归/鉴权/商业/婚姻
python3 tools/ui_check.py --only desktop  # 只跑某一组（含 --only commerce / --only marriage）
python3 tools/ui_check.py --serve         # 起 mock 页面，手动看；--auth 启动鉴权模式
python3 tools/ui_check_real_commerce.py   # 真服务（--auth off + ambient）商业面板取证
python3 tools/ui_check_real_marriage.py   # 真服务成功路径：talk 到 60+ → 求婚 → 配偶栏 → 和离
python3 tools/ui_check.py --only shopops  # 店铺经营专项（mock，16 项）
python3 tools/ui_check_real_shopops.py    # 真服务：能力探测；后端未就绪标 blocked_backend_not_ready
python3 tools/ui_check.py --only sect     # 门派专项（mock，21 项）
python3 tools/ui_check_real_sect.py --speed 4  # 真服务：押镖挣钱 → 声望/技能 → 立派 → 收徒 → 派俸
```

- mock 服务只改内存，不写 `runs/`、不启动世界 tick，避免与其它会话的确定性基线冲突。
- 证据目录：`reports/w4-ui/`（`checks.json`、桌面/移动/鉴权截图、`keyboard.txt`、`mobile-summary.json`）。
- 婚姻证据：`reports/w4-marriage/`（mock 与真服务截图；`real-marriage.json` 含每步真实请求/响应）。
- 店铺经营证据：`reports/w4-shopops/`（mock 截图与 `mock-shopops.json`；真服务 `real-*.png|json`）。
- 门派证据：`reports/w5-sect/`（`real-sect.json` 含能力探测、每步真实请求/响应与前后快照；`real-*.png`）。
- 真服务婚姻脚本按真实时间累积好感（4x 下约 29 分钟），不是 mock 成功路径；`--smoke` 只跑 talk 循环验证选择器。

## 10. 未覆盖 / 已知限制

- W1 契约已对齐；`/api/auth/*` 的真实响应样例来自 W1 收口说明与 `src/wsim/api/auth.py`，未在真实服务上做端到端联调（本轮仍用 mock 服务验收）。
- 不做第三方登录；不做口令找回。
- 引导③不做路径规划：若委托人不在相邻地点，只高亮当前可用出口之一，需玩家继续逐段移动；到达后自动进入④。
- 只读模式（`--readonly`）下交付会返回提案，不会真正产生资金变化，引导④进度会停在交付前，可用「跳过」结束。
- 桌面 1280×900、移动 375×812 已实测；其它尺寸只保证布局断点逻辑一致，未逐像素验收。
- 商业面板有 3 处后端字段缺口，UI 以明确文案兜底（不伪造数据）：
  1. `shop` 视图不返回自有店铺的雇工名单时，「雇工列表」显示「服务端尚未返回雇工名单」；
  2. `present_npcs` 不含技能与受雇状态，雇人行只显示好感（来自 `player.relations`），技能/受雇显示「接口未提供」；
  3. `_shop_view` 按物品去重，店铺库存件数无法可靠汇总，能汇总时才显示「N 件」，否则显示「件数未由接口提供」。
- 店铺经营五个动作（`restock` / `set_price` / `dismiss` / `withdraw` / `shop_report`）由后端另一路实现；
  UI 与 mock 验收已完成，真服务脚本会先探测，缺失时明确标 `blocked_backend_not_ready` 并跳过，
  不会用 mock 数据冒充真服务证据。`shop_report` 为空（开店不足一日）时如实显示空态。
- 真服务新世界的 NPC 技能要从 0 练到 0.15（`skill_too_low`），短跑世界雇人必然被拒；成功路径由 mock 验收覆盖。
- 婚姻有两处接口缺口，UI 只做明确兜底、不伪造：
  1. `present_npcs` / `nearby` 不返回 NPC 的配偶字段，候选列表对「别人是否已婚」显示「已婚状态：接口未提供」；能确定的只有玩家自己的配偶（显示「已婚（你的配偶）」）；
  2. 配偶当日陪伴优先读 `state.marriage.last_daily_tick / daily_note`；接口没给出时用配偶是否同地点近似，并在配偶块下注明口径。
- 门派有三处接口缺口，UI 只做明确兜底、不伪造：
  1. `GET /api/player/state` 不提供门派列表（无 `factions[]`），「加入门派」只能填门派 id；
     页面注明「暂不可用：后端未提供门派列表接口」；
  2. `player.sect.sect.disciples[]` 不返回逐人 `affection` / `trust`；有 `player.relations` 真实关系时
     按关系显示（与后端 `_bond` 同口径），没有任何关系记录时显示「暂不可用（缺
     disciples[].affection/trust）」；
  3. `disciples[]` 不返回逐人 `settled_today`，逐人结算状态显示「暂不可用（缺
     disciples[].settled_today）」；门级 `settled_today` 正常返回。
- `present_npcs[].bond` 走 `affection_between`，不含 `sworn` 关系；收徒裁决用的是所有 active 关系的
  `_bond`。UI 用 `player.relations`（真实返回字段）补齐好感/信任，使页面门槛与后端裁决一致；
  这不是新增字段，而是同一份真实关系数据的另一种聚合口径。
- `details.sect` 是 `{in_sect, sect}` 包装层（`docs/player-api.md §13` 同源）；UI 解包后取
  `treasury.balance` / `member_count`，不再把包装层当快照。
- 「休息」目前只是按钮占位（点一下提示「体力恢复中」，不发请求）；`rest` 动作接入后再换成真实请求。
