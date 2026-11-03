# tag `delivery-p2` 交付前独立复核（2026-11-03）

## 0. 结论

**总判定：有条件可交付。**

- `delivery-p2` 指向 `7137e62`，tag 之后只有一个报告提交；工作区干净，`ui/` 已纳入版本控制。
- 从 tag 干净重跑 10d / 72d，**所有自报数字逐项对上**：事件 2,841 / 12,607、state `62cad798…`、event `474043e6…`、`state_day10 == 10d state_final`、replay MATCH、framework 5/5、pytest 72 passed。
- 人设注入是真的：抽查 3 个 NPC 的 prompt 都含完整 6 节卡与 2 条台词原文；`persona_tokens` 与 prompt 注入段一致，不是日志凑数。
- 镖单修复是真的：`escort_iron_short` 发单 30 次、最后一天 day=71（第 72 天），过期单 206、完成 33、失败 4；`content/escort_routes.yaml` 与上一 tag 零差异。
- **唯一不能照单全收的是第 5 条（`treasury_guard` 的归因）**：结论方向对（期末 2,009 仍低于基线 2,014），但"没有实际止血"的机制描述是错的——护栏期内余额确实企稳，功劳来自工资流出骤降，而护栏唯一真正生效的采购暂停是在减少金库流入，方向是负的。
- 交付说明整体诚实，桩都标明了；但有一处算术错误（"60 个章节"实为 180）、一处口径未解释（931 决策 vs 754 次调用）、一处归因错误（护栏）。

**最先会崩的三处**（如果现在交给人玩）：

1. **经济贴着地板**：第 23 天起金库在 1,653–2,097 之间，期末 2,009 / 保底线 2,000。多跑几天工资链就断。
2. **开口无效**：`talk` 只进 outbox，没有执行器；Mock 也不读人设卡（只取 26 字进 `reason`）。玩家说什么都不会改变世界。
3. **任务板空转**：5 个模板 0 完成、206 张过期单，"发单→过期→重发"会占满玩家看到的委托列表。

复核未修改 `src/`、`content/`、`tests/`，未覆盖既有 `runs/`。新产物在 `runs/verify-delivery-p2/`；快照解包在 `/tmp/delivery-p2`。

---

## 1. tag 与工作区 —— 成立（附两点说明）

```
$ git rev-list -n1 delivery-p2
7137e62...
$ git cat-file -t delivery-p2
tag
$ git tag -v delivery-p2
object 7137e62...
type commit
tag delivery-p2
tagger ...
（以下无 “gpg: Good signature”）
错误：no signature found
$ git log --oneline delivery-p2..HEAD
05a330f docs: 交付说明...
$ git diff --stat delivery-p2..HEAD
 reports/delivery-p2.md | 336 +++++++++++++
 1 file changed, 336 insertions(+)
$ git status --porcelain
（空）
$ git ls-files ui
ui/README.md
ui/adapter.py
ui/index.html
ui/server.py
```

- tag 指向的 commit 就是交付 commit，tag 之后只有 `reports/delivery-p2.md` 一个提交：**成立**。
- `ui/` 已纳入版本控制（4 个文件）：**成立**。
- 说明 1：这是 **annotated tag，但没有 GPG 签名**（`git tag -v` 报 `no signature found`）。"tag 指向哪里"可信，"tag 没被改过"只靠仓库本身，不靠签名。
- 说明 2：tag annotation 里的 `content_hash=420ee2ff…` 与我解包后重算的 `420ee2ff…` 一致。

---

## 2. 从 tag 干净重跑 —— 成立（逐项一致）

```
$ cd /tmp/delivery-p2
$ .venv/bin/python sim/run_headless.py --days 10 --seed 42 --npc 0 \
    --out runs/verify-delivery-p2 --run-id verify-10d
$ .venv/bin/python sim/run_headless.py --days 72 --seed 42 --npc 0 \
    --out runs/verify-delivery-p2 --run-id verify-72d
```

| 指标 | 交付说明 | 独立重跑 | 判定 |
|---|---|---|---|
| 10d 事件 / 拒绝率 | 2,841 / 1.55% | 2,841 / 44 = 1.5488% | 成立 |
| 10d state_final | `ef8d206a…` | 同上 | 成立 |
| 72d 事件 / 拒绝率 | 12,607 / 1.46% | 12,607 / 184 = 1.4595% | 成立 |
| 72d state_final | `62cad7989deb…` | `62cad7989deb1d46…` | 成立 |
| 72d event_final | `474043e6bca5…` | `474043e6bca504e7…` | 成立 |
| `state_day10` | = 10d 的 state_final | `ef8d206a…`（完全相同） | 成立 |
| replay | MATCH | 10d MATCH、72d MATCH | 成立 |
| `framework_check` | 5/5 | PASS（10d 2,841 / 44 / 1.55%，K1 45/91=49.45%） | 成立 |
| `pytest` | 72 passed | `72 passed in 1.80s` | 成立 |
| K1 72d | 71/91 = 78.02% | 71/91，剔除一次性 70.15% | 成立 |
| 10d K1 | 49.45% | 45/91 = 49.45% | 成立 |
| 金库期末 | 2,009（保底 2,000） | 2,009 | 成立 |
| NPC 现金 / 商业体现金 | 31,499 / 2,009 | 31,499 / 2,009 | 成立 |
| 账户总额 / 销毁 / 铸造 | 42,716 / 4,434 / 47,150 | 42,716 / 4,434 / 47,150 | 成立 |
| 门派成立日 | 第 3 / 20 / 61 天 | day 3/20/61（镖行同盟/清议社/药行会） | 成立 |
| LOD 末态 | 25/4/1/1 | 相同；72d `decide_calls=980`、`processed_wakes=980`、`state_provider_calls=980`、`system_plan=0` | 成立 |
| 关系审计 | 检查 180 / 冲突 0 | `relations_all_kinds.relation_checks=180`，冲突 0 | 成立 |
| UI 回放 | 12,607 事件 / hash_ok | `events=12607 state_hash_match=True` | 成立 |

`runs/delivery-72d`（tag 自报 run）与我的 `verify-72d` 在事件数、哈希、K1、金库上逐项相同，确定性成立。

---

## 3. 人设注入 —— 成立

抽查 `bai_lusheng` / `su_hexiang` / `zhuo_bufan`，用 tag 代码重放 72d 事件到末态后构建 prompt：

```
--- bai_lusheng : card_chars=316 composed_chars=307 ptok=77 prompt_persona_tokens=77
   sections in prompt.user: 6/6
   quotes in prompt.user: 2/2
   persona_tokens == ceil(composed/4): True
--- su_hexiang : 6/6 节、2/2 台词、ptok=74
--- zhuo_bufan : 6/6 节、2/2 台词、ptok=79
```

- 全量 30 张卡：**180 个章节全部注入、60 条台词全部原文在场，无截断**（`compose_persona` 预算 450 token，未触发省略号）。
- 注入 token 分布 min/median/max = **69 / 73.5 / 79**，原始卡 71 / 75 / 80——与交付说明一致。
- `cost.jsonl`：754 条记录中 **750 条 `persona_tokens` 非零**；4 条为 0 全部来自 `ming_chabo`，该 NPC 本来就没有卡（30 卡 / 31 NPC）。
- 不是"整卡塞进日志凑数"：`persona_tokens` 来自 prompt 组装（`len(composed)/4`），不是 `len(card)`；`cost.jsonl` 本身不含卡文本。

两点补充：

- `decisions.jsonl` 的 `persona` 字段确实**冗余存了整张卡**（约 300 字符 × 754 条），但它是决策快照字段，不是 `persona_tokens` 的来源；`reason` 只嵌 26 字摘要。
- Mock 路径并不消费卡（只有 LLM 路径会），所以"注入生效"目前只是**注入链路生效**，不是"行为被人设改变"。交付说明已如实写明这一点。

---

## 4. 镖单修复 —— 成立

**（1）`open` 单过期真的生效**

```
$ grep -n "class SystemQuestFailAction" -A 20 src/wsim/actions/quests.py
if q.status == "accepted" and intent.tick > q.deadline_tick:
    effects.append(Effect("quest_fail", ...))
elif q.status == "open" and intent.tick > q.deadline_tick:
    # 无人接单且已过期：关闭模板占位，让后续 system_quest_post 能重发。
    effects.append(Effect("quest_fail", ...))
```

`effects.py:525` 对 `status=open` 的 `quest_fail` 记 `expired`。事件层：`system_quest_fail` 72 条（每天一条），累计 `quest_fail` effect 216 次；state 口径 expired 206、failed 10（216 = 206 + 10）✓。

**（2）发单日与计数**

```
delivery  escort_iron_short: posts=30 first=0 last=71   （第 72 天）
delivery  escort_medicine_temple: posts=21 first=0 last=70（第 71 天）
p2b-72d   escort_iron_short: posts=18 last=34（第 35 天）；medicine: 15 last=45（第 46 天）
b10-72d   escort_iron_short: posts=9  last=16（第 17 天）；medicine: 15 last=47（第 48 天）
```

- `escort_iron_short` 最后发单 = 第 72 天、`medicine_temple` = 第 71 天：与说明一致。
- 镖单：发起 38 / 到站 37 / 完成 33 / 失败 4 / 在途 1；`escort` 动作 0 拒绝：成立。
- 任务总盘：完成 203 / 失败 10 / 过期 206 / 在途 3（open 2 + accepted 1）：成立。
- 5 个零完成模板各发单 36 次 = 180，加上其他过期，206 的来源可闭合（422 总发单 = 203+10+206+3）。

**（3）没有提高镖银**

```
$ git diff --stat p2b-baseline..delivery-p2 -- content/escort_routes.yaml
（空）
```

`escort_routes.yaml` 与上一 tag 零差异；本次改动只有 `quests.py`（+4）、`effects.py`（+6）、`system.py`（+29/−2）、`economy.yaml`（+18）。
即修的是"过期后重发"，不是加重报酬。这一点交付说明没有夸大。

---

## 5. `treasury_guard` 是否名副其实 —— 部分成立（结论对，归因错）

**报告的三条事实，逐条复核：**

| 报告说法 | 实测 | 判定 |
|---|---|---|
| 进入第 23 天，余额 2,026 | day 23 balance=2,026，`system_treasury_guard` 仅 1 条（tick=3311） | 成立 |
| 活跃 50 天，退出 0 次，期末 2,009 | day 23→71 余额 2,026→1,966，期末 2,009；guard 事件 0 条退出 | 成立 |
| 11 次开店全在第 19 天前 | open_shop 成功 11 次，day 0–18（最大 day=18） | 成立 |
| 护栏拒绝次数 0 | 拒绝理由中 `treasury_guard` 出现 0 次 | 成立，但**是空过** |
| buy 363→246（基线→本 tag） | b10-72d 363，delivery 246 | 成立 |
| 分红第 30/60 天 effect=0，基线同样 0 | 两轮 `system_shop_dividend` effects 均为 0 | 成立 |

**为什么"拒绝 0"是空过**：`treasury_guard` 不是状态机，而是**动态阈值判断**：

```
src/wsim/actions/common.py
def treasury_guard_active(state, content):
    return treasury_balance(state) < treasury_guard_threshold(content)   # 2000×1.2 = 2400
```

余额低于 2,400 就算"激活"，高于就自动不算，不存在"进入/退出"状态。11 次开店都在第 19 天前、余额高于 2,400 时发生，所以"0 次拒绝"不能证明护栏拦得住；它只是**没被触发过**。`system_treasury_guard` 动作本身还写明"只做日志、不改变任何余额"。

**"止血无效"这个自我判断：方向对，机制错。**

- 对的部分：终点 2,009 仍低于基线 2,014，多跑几天仍会撞 2,000 保底线——"没有真正止住"成立。
- 错的部分：护栏期内余额不是继续崩，而是**几乎持平**。按天算：

```
day 1  11,768 → day 23  2,026       ≈ −533 文/天
day 23 2,026  → day 71  1,966       ≈ −1.25 文/天（区间在 1,653–2,097 震荡）
```

- 但持平不是护栏的功劳：`work` 工资支出从 **866 文/天降到 182 文/天**（work 动作 38.6 次/天 → 8.8 次/天），而护栏明确"工资与已承诺镖单不受限"，这条下降与护栏无关。
- 护栏唯一真正生效的暂停项是**采购**，而采购是金库的**流入**：guard 期 `buy` 流入从 239 文/天降到 30 文/天。这是在**减少回流**，方向是负的。
- 也就是说：护栏不是"无效"，而是"**有一个负向作用，被同期更大的正向变化掩盖了**"。交付说明把"没有实际止血"写成机制结论，容易让读者以为护栏没动作——实际上它动作了，只是动作方向错。

正确结论应为：**护栏在结果上没救回金库（期末还低 5 文），在机制上还拖了后腿；真正托住 2,000 的是工资支出收缩，不是护栏。** 这一条建议在交付说明里改写，并补一个真正的收入侧杠杆。

---

## 6. 交付说明是否诚实 —— 大部分成立，3 处偏差

**一致的项：**

- 护栏三条事实（1 次进入 / 0 退出 / 50 天 / 2,009 / 11 店全在第 19 天前）：数字全部对上。
- 卡偏短 71–80 token：实测原始 71 / 75 / 80、注入 69 / 73.5 / 79，对。
- 5 个零完成模板：`commission_herb_sorting`、`commission_stonework`、`errand_silk_sample`、`inquiry_luo_marriage`、`procure_ginseng` 各 36 单、完成 0，对。
- 10 天 K1 49.45%：45/91，对。
- "没有把桩写成已完成"：LLM 默认关闭（`enable_llm=False`，Provider 未启用时客户端不启动）、MCP 双人为内存 token、UI 是离线 run 快照、`talk` 不进世界——四件事都写进了"当前边界/未达标"，没有伪装成已完成。UI 回放 `12607 / hash_ok=True` 也复现了。

**偏差 1（算术错误）**："60 个章节（30 卡 × 6 节）全部在场"——30×6 = **180**，不是 60。实测 180 节、60 条台词全部注入。"60" 应为笔误或把台词条数错当章节数。

**偏差 2（口径未解释）**："决策数 931 / reason 覆盖率 1.0" 与 "cost calls 754" 并列出现，但没说明差额。实测：

```
decisions=931（754 主决策 + 177 replan）
cost.jsonl=754 条；177 次 replan 是本地确定性改计划，不建 prompt、不调模型、不记成本
```

这不是造假，但读者会误以为 931 次都花了钱。建议写成"754 次模型调用 + 177 次本地重规划"。

**偏差 3（归因错误）**：`treasury_guard` 的"止血无效"机制描述，见第 5 节。

**另外**：tag 是 annotated 但**未签名**；说明里没有声称签名，因此不算不诚实，只是交付完整性靠仓库信任而非签名。

---

## 7. 总判定与前置条件

**总判定：有条件可交付。**

可交付的理由：冻结可复现（同 tag 同 seed 逐位一致）、回放 MATCH、72 测试与 5/5 门禁通过、人设注入与镖单修复都是真机制、桩边界写得清楚。

三个条件（按优先级）：

1. **修交付说明的第 5 节归因**（护栏不是"无效"而是"负向被掩盖"），并给金库补一个收入侧杠杆；否则"再跑几天就停摆"是确定会发生的事。
2. **明确"可玩"的边界**：`talk` 只是 proposal、Mock 不读卡。如果"交给人玩"包含对话，需要先接执行器/真实模型，否则 UI 只能看不能玩。
3. **任务板治理**：206 张过期单 + 5 个零完成模板，玩家第一眼就会看到大量接不了的委托；至少加"过期不下发/降低发单频率"或补足可达性。

---

## 附：证据文件

```
runs/verify-delivery-p2/
├── verify-10d/  verify-72d/     # 独立重跑（events.sqlite / cost.jsonl / decisions.jsonl / summary.json）
├── persona_check.txt            # 全量 180 节 / 60 台词注入检查
├── persona_prompt_check.txt     # 3 NPC prompt 抽查与 ptok 一致性
├── event_analysis.txt           # 开店 / 护栏 / 镖单发单日 / 过期计数
├── treasury_flow.txt            # 金库分 verb 流入流出、guard 期拆分
├── summary_details.txt          # 任务 / 镖单 / 门派 / 成本明细
├── crossrun.txt                 # p2b / b10 / delivery 发单日与拒绝对照
├── k3_recompute.txt             # K3 v1.1 / v1.0 兼容 / 敏感性 独立复算
├── claims2.txt  final_checks.txt
└── replay_pytest_framework.txt
```
