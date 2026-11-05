# CHANGELOG

> 制度：每次改动都必须 commit 并推送到 `origin`，且在本文件留一条记录。版本按时间倒序。
> 每条必须写清：**改了什么、为什么改、验证方式、commit 短哈希**。
> 数字只允许取自 `git log`、`docs/RELEASE-NOTES-*.md`、`docs/DELIVERY-TEST-*.md`、`reports/`；
> 查不到的一律写“未记录”，不得估算或外推。
> 跨版本比较事件数 / 金额，必须同时标注当时的 `content_hash` 与 `state_final` / `event_final` 哈希。
> 提交流程与 tag 规则见 [docs/GIT-WORKFLOW.md](docs/GIT-WORKFLOW.md)。

---

## [功能] 2026-11-05 — 世界自走成为首发默认体验

- **改了什么**：`./start.sh` 默认同时拉起 API、前端与后台自走（`XKX_AMBIENT=0` 关闭，
  关闭态与旧版一致）；ambient 日志落 `runs/<RUN_ID>/logs/ambient.log`，API/前端各自落文件；
  API 就绪后 ambient 后台独立启动，退出时先收 ambient 并 `wait` 收割；旧自走目录自动归档。
  CPU 护栏改为 70% 阈值三档动作：补睡降速 → 持续超限暂停 → 低于恢复线自动恢复；
  新增 `ambient_status.json`、`parent_pid` 看护、`cpu_pauses/cpu_resumes`。
  `/api/health`、`/api/service/info`、`ui/player` `/api/health` 只**追加** ambient 字段，
  `ok/mode/readonly/profile_version/upstream_ok` 不变。
- **为什么**：此前默认不启用调度器，玩家解包起服务后世界静止，与产品核心设定相反；
  同时避免自走过载时拖死服务端、进程退出后残留僵尸。
- **验证**：干净 worktree（HEAD=`fea1d8f`）全量 pytest **429 passed / 10 skipped / 0 failed**；
  `scripts/selfcheck.sh` **4/4 PASS**；`./start.sh` 一条命令后 `/api/health`
  `ambient=running` 且 7s 内 `ambient_tick 0→1`；`XKX_AMBIENT=0` 为 `stopped` 且无 ambient 进程；
  300 ticks（seed 42）：1119 事件 / 966 NPC 自主事件 / 28 个活跃 NPC / 16 种动词 / 0 LLM；
  玩家第 149 tick 进入、209 退出：progress 0..299 连续，0..149 与无玩家对照逐字节一致，0 回滚；
  1x 3 分钟进程 CPU 0.19%，注入忙等负载后 70% 阈值实测 3.68s 暂停、2.24s 自动恢复。
- **commit**：`aee9bbe`（调度器/护栏）、`90e4404`（health）、`fea1d8f`（start.sh）。

---

## [模拟] 2026-11-05 — 委托公平轮转，10 天零接单模板 16→0

- **问题**：`Planner._quest_step` 按 `q.id` 字典序扫开放委托，`procure_*` /
  `teach_*` 等前缀靠后的模板永远排队尾；10 天 seed42 开启态有 16 张模板零接单
  （其中新增的 33 张里有 9 张饿死），6 件物品零成交。另有 2 天期限模板的
  `not_at_giver` 静默拒绝：接单步骤在上工/午饭之后，发起人已走开。
- **修复**：开放委托按模板 `last_served_tick` 最久未服务优先轮转（平局按
  `quest id` 稳定排序，不依赖 dict 顺序或墙钟）；接单/执行步骤移到当天上工之前。
- **验证**（10 天 seed42，`content_hash` 开启态 `3fd99946b44a…`、关闭态
  `7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`）：
  - 开启态：接单 106→181，distinct 模板 44→60，零接单 16→0（新增 33 张里 9→0），
    零成交物品 6→4；state/event `d92b4579…` / `1cb069d7…`，illegal 0.0；
    两次重跑哈希一致。
  - 关闭态：K1 **62/91 = 68.13%**（护栏 63.13%，未改阈值），事件 2934→2948；
    state/event `331c2e1c…` / `5a122bec…`；两次重跑哈希一致。
- **基线**：旧 800-event golden（V2）原值保留并显式 skip；新增 V3 基线
  （693 events，state `a8aecf44…` / event `ba66bb4d…`），漂移原因见
  `tests/test_p3e_golden.py` 文件头：任务顺序前移导致 NPC 一日行动顺序变化，
  sell 971→967、buy 820→793、burned 127→81、business_cash 12184→12599。
- **验证计数**（干净 HEAD `fea1d8f` + 本提交）：pytest **429 passed / 12 skipped**；
  `scripts/selfcheck.sh` **4/4 PASS**。
- **commit**：`e1d6d2b`。

---

## [安全] 2026-11-04 — 读端点越权读取已修复

- **问题**：服务模式下读端点匿名且接受任意 `account_id`，读路径不解析令牌，
  存在读端点越权读取（可见面过宽）。
- **修复**：匿名可见面收窄为公共摘要（白名单 `ok`/`name`/`place`/`activity`/
  `idle_seconds`/`online`/`sect_name`/`shop_name`/`server_tick`）；带令牌强制
  `account = 令牌账号`，不一致 403 `auth_account_mismatch`；`messages` 匿名返回空列表；
  `quests` 匿名只给公共开放任务；`intents` 服务模式强制令牌（匿名 401
  `auth_missing_token`）。legacy 模式（`--auth off`）行为不变。
- **契约**：`docs/AUTH-API-W1.md` §3「读端点的可见面（服务模式）」。
- **验证**：全量 pytest **422 passed / 10 skipped / 0 failed**；`scripts/selfcheck.sh`
  **4/4 PASS**；匿名 `state` 响应体逐键等于白名单，且 grep 不到
  `wallet|items|skills|relations|active_quests|growth|today`。
- **commit**：`133333e`。

---

## [安全] 2026-11-04 — W5 崩溃演练明文令牌清理

- **事件**：`deploy/crash_drill.sh` 曾把用 `drill-secret` 登录得到的完整 Bearer token
  写入 `reports/w5/crash-drill/token.txt`（43 字节，**未入库、未推送**）。
- **处置**：删除该文件；源脚本改为只在进程内存/局部变量持有令牌，落盘仅
  `token-digest.txt`（sha256 + 末 4 位）；`/api/auth/me` 证据改为
  `status/ok/expires_at`（不再写 session_id）；演练结束执行
  `DELETE FROM sessions`，令牌即时失效，不依赖 TTL。
- **防复发**：`.gitignore` 增加 `**/token.txt`、`**/*.token`、`reports/**/token*`。
- **验证**：吊销前旧令牌 `/api/auth/me` HTTP 200；`DELETE FROM sessions` 后同一令牌
  HTTP 401 `auth_invalid_token`；`bash -n deploy/crash_drill.sh` 通过；新产物无完整令牌。
- **TTL 说明**：该令牌 `expires_at=1793885305`（2026-11-05 13:28 UTC，约 24h），
  与 60 秒断线重连宽限期无关——宽限期只影响 `disconnected` 判定，不延长令牌寿命。
- **commit**：`41ae2b1`（本次安全提交）。

---

## [记账] 2026-11-04 — `466c807` 混装提交审计（W2 + W3/W3b + content）

- **commit**：`466c807`（`feat(content): 扩展包改为引擎原生加载`，提交时间 2026-11-04 20:53）。
- **审计结果**：该提交实际包含 **9 个文件**，与“误吞 W2 + content + W3 + W4 + W5”的描述**不完全一致**：
  本次审计**未在该提交中发现 W1 / W4 / W5 / 打包文档 / agent 自提案文件**。
- **文件归属**
  | 文件 | 归属 |
  | --- | --- |
  | `docs/AMBIENT.md` | W2 调度器（自走运行说明） |
  | `src/wsim/sim/scheduler.py` | W2 调度器 |
  | `tests/test_w2_scheduler.py` | W2 调度器 |
  | `tools/ambient_accept.py` | W2 调度器（验收器） |
  | `tools/ambient_run.py` | W2 调度器（运行器） |
  | `src/wsim/core/content.py` | W3 内容（原生包加载） |
  | `src/wsim/core/content_manifest.py` | W3 内容（包清单/校验） |
  | `tools/content_scale.py` | W3 内容（扩容工具） |
  | `tests/test_w3b_native_packs.py` | W3b 人物卡（原生加载测试） |
- **为什么会出现混装**：提交信息只描述“内容包原生加载”，却同时带入 W2 调度器全套文件（`scheduler.py`
  942 行、ambient 工具与文档）。从文件构成判断，是多个并行会话共享同一工作区 / index 时，提交未按
  pathspec 限定范围，把另一条线已就绪的改动一起打包（**推断**，非提交者声明）。
- **影响**：`466c807` 无法按“一条线一个 commit”回溯；但 9 个文件均已入库，内容没有丢失。
- **处置**：不改写历史（amend / rebase / filter / 强推一律不做）；以本条文件归属表补足追溯粒度，
  后续提交须用 `git commit -- <paths>` 限定范围（见 `docs/GIT-WORKFLOW.md` §2）。
- **验证**：干净 HEAD（`d7f3707` 的 `git archive`）上 `pytest` **385 passed, 10 skipped**、
  `selfcheck.sh` **4/4 PASS**，说明混装内容与当前基线兼容。

---

## [未发布] 2026-11-04 — W1 引擎变更（账号 / 存档 / 内容 hash 闸门）

- **改了什么**
  - `88fdbfb` 账号与令牌存储：PBKDF2 口令哈希 + sqlite，口令不落明文（`src/wsim/api/auth.py`、
    `tests/test_w1_auth.py`）。
  - `ac40a9c` 事件日志回放恢复玩家存档，重启不丢档（`src/wsim/api/persistence.py`、
    `src/wsim/api/session.py`、`tests/test_w1_persistence.py`）。
  - `23e1d05` 内容 hash 不匹配时拒绝启动：退出码 3 + 契约日志（`src/wsim/api/__main__.py`、
    `tests/test_w1_hash_mismatch.py`）。
- **为什么改**：玩家账号与存档在重启后丢失；口令需要不可逆存储；内容包与代码版本不一致时静默启动会污染世界。
- **验证方式**：三个提交均附新增测试文件，但提交信息**未记录**实跑结果 → **未记录**。
- **commit**：`88fdbfb`、`ac40a9c`、`23e1d05`。
- **备注**：以上提交由并行工作流完成，本条目按仓库制度补记，未改动其代码。

---

## 2026-11-04 — chore(git): 建立 GitHub 推送链路与变更记录制度

- **改了什么**
  - 为本地仓库添加 `origin = https://github.com/lyinet/mud-world`，推送 `master` 全量历史与全部已有 tag。
  - 新增本文件 `CHANGELOG.md`：按版本倒序补记 p3f–p3j 里程碑。
  - 新增 `docs/GIT-WORKFLOW.md`：提交规范、推送时机、tag 规则、不入库清单、本地钩子说明。
  - 安装本地 `.git/hooks/post-commit`：提交后在后台 `git push origin HEAD`，失败只写 `.git/push.log`，不阻塞提交。
- **为什么改**：用户硬规则要求“每次修改都推送、每次都详细留痕”，且远程仓库刚建立、此前无任何 commit 对应。
- **验证方式**：`git remote -v` 含 origin；`git push -u origin master` 与 `git push origin --tags` 成功；
  `git ls-remote origin` 的 `refs/heads/master` 与本地 `HEAD` 一致；14 个 tag 全部出现在远程。
- **commit**：`f12424f`（制度与两份文档）；`docs(changelog):` 补记提交回填本条哈希与钩子安装命令修正。

---

## 2026-11-04 — delivery-p3j（tag `delivery-p3j` → `184859d`）

- **改了什么**
  - `5635c26` 目标体量校准：长线目标 target 从 600 文 / 0.6 / 40 点降到 142 文 / 0.033 / 12 点，
    按 10 天有效产能 P60 的 6–8 成设定。
  - `bc7013c` 玩家成长线（声望 / 技能 / 资产各 5 级）与 NPC 里程碑链（一级完成后自动建二级、三级）；
    新增维护成本：店铺月租 30 文/家、门派月费 6 文/人、技艺生疏 −0.002、关系衰减 −1.0。
  - `c999fb6` 在线玩家委托供给修复：过期单自动下架、日界按模板补发、拒绝原因可诊断
    （`quest_expired` / `reputation_too_low` / `too_many_quests`）；技能 L2 门槛 0.36 → 0.42、
    资产 L2 门槛 400 → 600。
  - `edb3560` 对外口径与 `known-limitations` 更新；`184859d` p3j 干净解包实测记录。
- **为什么改**：p3i 的目标体量按满产假设设定，实际 10 天产能下大量目标空转；委托会过期下架导致玩家无单可接；
  成长线缺少可见的下一阶段目标与面板。
- **验证方式**（`docs/DELIVERY-TEST-p3j.md`、`docs/RELEASE-NOTES-p3j.md`）
  - `python3 -m pytest`：**321 passed, 8 skipped**；`selfcheck.sh` 4/4 PASS；`framework_check.py` 6/6 PASS；
  - 规则臂 K1 **49.45% → 65.93%**，完成目标 **20 → 43**；攒钱 / 学手艺 / 结关系完成率
    **3.8% / 0% / 0% → 30.8% / 31.2% / 50.0%**；空转目标 38 → 23，未创建目标 22 → 15；
  - 事件数 3366 → 2707（−19.6%）；7 天委托压力测试 14 接 / 14 交 / 0 拒绝；
  - 新增基线 `GOLDEN_MILESTONE`（800 events）与 `PLAYER_SUPPLY_GOLDEN`；旧基线（824 / 791 / 796）显式 skip。
- **内容冻结**：`content_hash = 7ec670c1c83c7b99e9bee35f84602bd7a68ef22126b29c9003529809b2c3d53f`
  （31 NPC / 41 物品 / 9 地点 / 27 任务模板）；种子 2 日世界 **836 事件 / NPC 自主行动 736**。
- **未达项（如实保留）**：模型在 K1 维度零增益；战略层实验 FAIL（PCR 22.73% < 28%），模型层机制实验永久收工；
  对话与好模板持平（非平手 55.9%，Wilson CI 44.1%–67.1%），默认走模板。
- **commit**：`5635c26`、`bc7013c`、`c999fb6`（引擎），`edb3560`、`184859d`（文档）。

---

## 2026-11-04 — delivery-p3i（tag `delivery-p3i` → `d13ff53`）

- **改了什么**
  - `19c298b` 目标生命周期审计（`reports/goal-lifecycle-audit.md`）。
  - `5833eca` 目标覆盖护栏 v2 与类型配额；**代码保留、默认关闭**。
  - `20dc739` 内容扩充：新增 10 件物品（麻绳 66 笔、墨条 9 笔、小米 2 笔、艾条 2 笔成交，另 38 次消耗）、
    4 步引导链、6 类日常委托、3 条长线目标；31/31 NPC 有关系边
    （朋友 47 / 对手 16 / 债主债户各 13 / 师徒各 5 / 亲族 8）。
  - `64365f8` 供需 + 库存驱动物价接入运行时，单日 ±20% 护栏。
  - `64458bd` 冻结快照 K1 对照与收工判定；`79caed3` 对外口径；`d13ff53` 干净解包实测。
- **为什么改**：p3h 的内容深度不足（摊位 / 引导 / 长线缺失），物价是静态的；K1 连续两轮未达标，需要冻结快照做归因，
  避免把内容扩充带来的提升误记为模型收益。
- **验证方式**（`docs/DELIVERY-TEST-p3i.md`、`docs/RELEASE-NOTES-p3i.md`）
  - `python3 -m pytest -q`：**301 passed, 4 skipped**；`selfcheck.sh` 4/4 PASS；framework 6/6 PASS；
  - 确定性 2 日 seed：事件 **633 → 838（+205，+32.4%）**；2 日 15 件商品中 **12 件有价差**，最高 **+12.5%**；
  - 新增基线 `GOLDEN_P3I_PRICE` = **791 events**（state `86871faf…`、event `ee7d0a90…`、sell 1145 / buy 842）；
  - 冻结快照下规则臂与模型臂 K1 **同为 56.04%（51/91）**，提升归因于内容扩充而非模型；
    模型 438 calls / **$3.03**，第 7 天触发 $3.0 hard 中止。
- **内容冻结**：`content_hash = fac4ac31fec9db5720d829d79827b215f5d0aee217be5fcb57bfdf38165ef8dd`
  （41 物品 / 31 NPC / 9 地点 / 27 任务模板 / 关系边 106）。
- **未达项（如实保留）**：K1 三次尝试均未超过规则臂，模型在该维度零收益、纯成本；对话结论不变（默认模板）。
- **commit**：`19c298b`、`5833eca`、`20dc739`、`64365f8`（引擎/分析），`64458bd`、`79caed3`、`d13ff53`（文档）。

---

## 2026-11-04 — delivery-p3h（tag `delivery-p3h` → `c19585b`，对外首发）

- **改了什么**
  - 对话层：`d780dcf` 接入 `text_guard` 与统一 system prompt（方括号主题词标签 / Markdown / 括号动作 /
    第三人称旁白两侧均为 0，角色口号去重）；`456c093` 收细诉求路由（问旧事 / 问路 / 借物 / 赊账拆开）并重建 r3b；
    `c4d1e66` 盲评扩到 70 对；`451aed3` 构建 r4 最终盲评包。
  - 自主目标（A3）：`f1010a0` 模型目标选择权与动作留存；`9242c1e` `not_together` 丢弃/改选、决策槽回放、成本硬闸；
    `c736849` 目标覆盖护栏与第二轮实测；`335a4ee` 竞态 trade 记 `stale`、intent 归零与护栏收口。
  - `1c046fd` 入库盲评与评测证据；`f22ad3d` 对外口径与打包范围；`205bfe4` 出包跳过 sqlite 二进制副本；
    `2c096c3` 对话评测输入改为 `runs/` 优先、`reports/evidence/a2-dialogue-30/` 回退，保证包内可跑；
    `c19585b` 干净解包实测记录。
- **为什么改**：p3g 存在对话标签泄漏、诉求路由过粗、盲评样本仅 30 对；A3 的 trade 竞态被误计为非法；
  干净包内 5 个测试硬编码 `runs/` 路径导致失败。
- **验证方式**（`docs/DELIVERY-TEST-p3h.md`、`docs/RELEASE-NOTES-p3h.md`）
  - 包内 **287 passed**；`selfcheck.sh` 4/4 PASS；framework 6/6 PASS；
  - K6：严格 **1.142%** / 含剪枝 **1.755%**（均 < 2% PASS），已执行非法 0；53 条 trade 竞态全部记 `stale`；
  - K1：模型 **41/91 = 45.05%** vs 规则 **45/91 = 49.45%**（−4.40pp，FAIL），停止规则生效；
  - K3 转 `diagnostic_only`（verb 17 vs 16、entropy 2.7231 vs 2.5842、pairwise JS 0.1448 vs 0.1087，
    对象层 entropy 5.0185 vs 5.0478）；
  - R5 盲评 70 对：模型 38 胜 / 模板 30 胜 / 平手 2，非平手胜率 **55.9%**，Wilson CI **44.1%–67.1%**；
    模型 70/70 更长（49.0 vs 31.0 字），17 条“更长但更空”全在模型侧；
  - 整 run 回放 `c4fc017c…`、`9b921eb1…` 均 MATCH，回放成本 0；A3 跨轮累计约 **$5.80**，对话生成约 **$0.33**。
- **content_hash**：未记录。
- **未达项（如实保留）**：K1 未达规则臂；K3 对象层 entropy 略低；模型侧套路化动作/口号 87 次。
- **commit**：`d780dcf`、`456c093`、`c4d1e66`、`451aed3`、`f1010a0`、`9242c1e`、`c736849`、`335a4ee`（引擎/评测），
  `1c046fd`、`f22ad3d`、`205bfe4`、`2c096c3`、`c19585b`（文档/打包）。

---

## 2026-11-04 — delivery-p3g（tag `delivery-p3g` → `60e8596`，内部快照）

- **改了什么**
  - `60e8596` 披露 fake run 世界改写、对外改用**含剪枝 K6**口径、补 golden 基线、全量 `blocked` / `pruned` 审计。
  - `7c32fb7` 透传 decision 批处理的 thinking 遥测；`d7f691e` 增加 K1 在局 / 已创建 / 剔除一次性口径；
    `0aa6b19` TalkAction 接入模型回复并支持 8s 超时回退；`cc3a475` 锁定 dialogue 开启路径 golden。
  - 对话 fallback 与路由：`98f0cdf`、`fe77e25`、`e0882b5`、`8f35270`、`e2151e0`、`7ff56c3`、`193fc7a`；
    `2dce941` 重建 R2 盲评包；`7251859` 修正 A2 成本口径。
  - `88ceebe` 更新 p3g 说明与已知限制。
- **为什么改**：原“录制 intent 重跑 = 确定性对照”的表述失实（重跑 220 events、`replay_miss=18`）；
  同 tick 调度里 `talk` 之外动词的顺序依赖制造了无关世界漂移；`blocked` / `pruned` 记录被截断无法对账。
- **验证方式**（`docs/DELIVERY-TEST-p3g.md`、`docs/RELEASE-NOTES-p3g.md`）
  - `pytest` **194 passed**；framework **6/6**；`selfcheck.sh` 4/4 PASS；
  - golden 基线 **824 events**（state `1bd870c4…`）；事件回放 222 events MATCH（state `e607d200…`）；
  - 对外含剪枝 K6：fake 2 天 **7/824 = 0.85%**、deepseek 2 天 **2/222 = 0.90%**（PASS）；严格口径 0.24% / 0.00%；
  - 世界漂移 816 → 824：sell 1165 → 849、buy 956 → 795、burned 112 → 113、商户现金 11925 → 12058。
- **content_hash**：未记录。
- **未达项（如实保留）**：K1 7/91 = 7.69%（FAIL）；`dialogue` 槽 0 次调用，文笔未评测；
  deepseek 新旧两次为独立采样，2 条 `blocked → pruned` 是换桶而非行为改善。
- **commit**：`60e8596`（tag），`7c32fb7`–`193fc7a`（引擎/评测），`88ceebe`（文档）。

---

## 2026-11-04 — delivery-p3f（tag `delivery-p3f` → `943645f`，内部里程碑）

- **改了什么**
  - `1c88cae` 同 tick 调度只保留 `talk` 优先，并补全 `blocked` / `pruned` 记录。
  - `943645f` 首发生成交付包：`start.sh` 一键启动、`scripts/world_feed.py` 世界观察、
    玩家前端断线提示、`tools/build_dist.py` 出包、`docs/QUICKSTART.md` 重写、
    `docs/known-limitations.md`、干净解包实测。
  - `b34317b` 事后标注该版本为过渡版本（包含他人 `headless.py` 在途改动，不作对外最终版）。
- **为什么改**：需要可交付的“一键启动 + 快速开始 + 可玩自检 + 断线提示”，并固定版本与已知限制。
- **验证方式**（`docs/DELIVERY-TEST-p3f.md`）
  - 干净临时目录解包后首次 `./start.sh` 自动建 venv、装 `requirements.lock`、生成 2 日种子世界；
  - 种子世界 **633 条事件 / NPC 自主行动 541 条**；健康检查 `mode=live, upstream_ok=true`；
  - 玩家闭环 enter → state → move → talk → quest_accept 全部 ok；断线后 `mode=degraded, upstream_ok=false`，前端仍 200；
  - `selfcheck.sh` 4/4 PASS：内容校验、**pytest 193 passed**、framework 6/6、玩家闭环。
- **content_hash**：未记录。
- **未达项**：浏览器自动化截图接口报 viewport 不可用（BLOCKED），未保存截图，改用 QUICKSTART 手工走查步骤。
- **commit**：`1c88cae`、`943645f`（交付），`b34317b`（标签说明）。

---

## 更早版本（不逐条展开）

`delivery-p2`、`delivery-p2.1`、`delivery-p2.2`、`p2b-baseline`、`delivery-p3a`–`delivery-p3e`
均已随仓库推送，tag 保留不改写。详细记录见 `reports/delivery-p2*.md`、
`reports/p3a-report.md`–`p3e` 系列与对应 `specs/`；本文件从 p3f 起补记，更早条目未逐条整理。
