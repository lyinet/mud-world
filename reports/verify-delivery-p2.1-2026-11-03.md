# P2.1 独立复核报告

- 复核对象：tag `delivery-p2.1`（commit `33a51f5f8ef2f291ddf7357135d4498b821d8892`）
- 交付说明：`reports/delivery-p2.1.md`
- 复核日期：2026-11-03
- 方法：`git archive delivery-p2.1 | tar -x -C /tmp/delivery-p2.1` 解包到独立目录；
  所有重跑输出到 `runs/verify-delivery-p2.1/`；`talk` 在 run 副本上执行，原 run 不被修改；
  未改 `src/`、`content/`、`tests/`。
- P2 基线：使用上一轮独立复核留下的 `runs/verify-delivery-p2/verify-72d`（同一 seed/天数，
  从 `delivery-p2` tag 干净重跑），不用作者自己的 run。

## 0. 结论速览

| # | 复核项 | 判定 |
|---|---|---|
| 1 | tag 完整性 | **成立**（tag 后是 2 个 docs commit，不是 1 个；tag 未签名） |
| 2 | 干净重跑 | **成立**（全部数字/哈希/回放/门禁一致） |
| 3 | `talk` 真落地 | **成立**（限幅确为 clamp，不是覆盖；但证据只存在于临时 session） |
| 4 | 任务板与金库 | **部分成立**（数字全对；"税源自转折叠"的解释不成立） |
| 5 | K1 为何下滑 | **部分成立**（分子分母与 3 个目标可定位；归因写错了） |

**总判定：有条件可交付。** 交付物本身可跑、可回放、守恒为 0，`talk` 不再是提案；
但交付说明有两处**因果解释与实测相反**，不改文档就交付会误导验收方。

---

## 1. tag 完整性：成立

```bash
$ git -C world-sim rev-list -n1 delivery-p2.1
33a51f5f8ef2f291ddf7357135d4498b821d8892

$ git -C world-sim log --oneline delivery-p2.1..HEAD
0c9b0d6 docs(P2.1): 勾选 T7 收尾
0653397 docs(P2.1): 回填交付报告与任务勾选

$ git -C world-sim diff --stat delivery-p2.1..HEAD
 reports/delivery-p2.1.md | 268 +++++++++++++++++++++++++++++++++++++++++++++++
 tasks.md                 |  16 +--
 2 files changed, 276 insertions(+), 8 deletions(-)

$ git -C world-sim status --porcelain
（空）

$ git -C world-sim tag -v delivery-p2.1
...
commit=33a51f5f8ef2f291ddf7357135d4498b821d8892 content_hash=ff09d8a814ce6f514294c2952d043bf11fc10bdd2338f000509b3485e18b2c9d
错误：no signature found
```

解包后独立重算：

```bash
$ cd /tmp/delivery-p2.1 && python -c "load_content(...).content_hash"
content_hash: ff09d8a814ce6f514294c2952d043bf11fc10bdd2338f000509b3485e18b2c9d
npcs: 31 locations: 9
```

- tag 指向 `33a51f5`：成立。
- tag 后只有文档提交：成立，但是**2 个**（报告 + `tasks.md`），不是"只有 1 个"。
- 工作区干净：成立。
- tag 附注 `content_hash` 与解包重算一致：成立。
- 备注：tag 是 annotated 但**未签名**（`no signature found`），本轮不影响内容校验，但不宜称为"已签名冻结"。

---

## 2. 干净重跑：成立

```bash
$ cd /tmp/delivery-p2.1
$ python sim/run_headless.py --days 10 --seed 42 --npc 0 \
    --out ../world-sim/runs/verify-delivery-p2.1 --run-id verify-10d --dump-state
$ python sim/run_headless.py --days 72 --seed 42 --npc 0 \
    --out ../world-sim/runs/verify-delivery-p2.1 --run-id verify-72d --dump-state
$ python tools/replay.py --run ../world-sim/runs/verify-delivery-p2.1/verify-10d
$ python tools/replay.py --run ../world-sim/runs/verify-delivery-p2.1/verify-72d
$ python tools/framework_check.py
$ python -m pytest -q
```

| 项 | 交付说明 | 独立重跑 | 判定 |
|---|---|---|---|
| 10d 事件 | 2,841 | **2,841** | 一致 |
| 10d 拒绝 | 44 / 1.549% | **44 / 1.5488%** | 一致 |
| 10d `state_final` | `2f7d5eb13f2b…` | **`2f7d5eb1…`** | 一致 |
| 10d `event_final` | `aa67d879f9da…` | **`aa67d879…`** | 一致 |
| 72d 事件 | 12,777 | **12,777** | 一致 |
| 72d 拒绝 | 189 / 1.4792% | **189 / 1.4792%** | 一致 |
| 72d `state_final` | `51fb61e6ef26…` | **`51fb61e6…`** | 一致 |
| 72d `state_day10` | `2f7d5eb1…` | **`2f7d5eb1…`** | 与 10d 一致 |
| replay 10d / 72d | MATCH | **MATCH / MATCH** | 一致 |
| framework | PASS 5/5 | **PASS 5/5** | 一致 |
| pytest | 87 passed | **collected=87，exit 0** | 一致 |

72d 的其余关键值也与说明一致：`decisions_total=959`、`model_calls=779`、`replan=180`、
NPC 现金 **31,563**、账户总额 **42,842**、`minted=47,150`、`burned=4,308`、
工资 27,640/27,640/0、镖单 37/36/34、门派第 3/20/61 天、成本 779/57,588/1.9938。

---

## 3. `talk` 是否真落地：成立（但证据是临时的）

在 72d run 的**副本**上通过 `ui/adapter.py` 的 `dialogue()` 连跑两轮，原 run 不动：

```bash
$ python - <<'PY'
sys.path.insert(0,'src'); sys.path.insert(0,'ui')
a = WorldUIAdapter('/tmp/talk-run3')          # verify-72d 的副本
r1 = a.dialogue('liu_sanniang','三娘，多谢你照应。')
r2 = a.dialogue('liu_sanniang','今日气色不错，辛苦了。')
PY
state_hash_match: True  tick: 10367
R1 seq=12778  outcome=ok  verb=talk  affection=1.0  trust=40.5
R2 seq=12779  outcome=ok  verb=talk  affection=2.0  trust=41.0
talk 记忆=4 条（每轮 2 条：talk:player / talk:npc）
账户总额 before=42842  after=42842（53 个账户）
```

事件里的 effect（第二条 `talk`）：

```json
[{"op":"memory_add","data":{"npc":"liu_sanniang","source":"talk:player","text":"访客说：今日气色不错，辛苦了。","tick":10367}},
 {"op":"memory_add","data":{"npc":"liu_sanniang","source":"talk:npc","text":"我答：…「阿是这话，你也听得的？」","tick":10367}},
 {"op":"relation_upsert", ... "kind":"acquaintance", "affection_delta":1.0, "trust_delta":0.5},
 {"op":"talk_ledger", ... "day":71, "day_affection":2.0, "tick_affection":2.0},
 {"op":"create_entity", ... "player:visitor"} / set_field]
```

回放（把 Writer 的 session 库用 sqlite backup 拷出后独立重放）：

```
writer_after  : 7ba2caa666cd7c2bbf9f84eba9a7791d763445da659555b01116d204485cfd9f
replayed_hash : 7ba2caa666cd7c2bbf9f84eba9a7791d763445da659555b01116d204485cfd9f
events_in_copy: 12779
MATCH
```

**"第 2 轮 affection 被压回 0" 是 clamp，不是覆盖。** 我做了两组对照：

```bash
# 同一 tick，显式请求 +2 两轮
same-tick round1: aff=2.0  tick_aff=2.0
same-tick round2: aff=2.0  tick_aff=2.0      # 值保持 2.0，不是被写成 0

# 同一日连续 5 个 tick，每 tick 请求 +2
tick 10224..10227: aff=2.0 → 4.0 → 6.0 → 8.0
tick 10228:        aff=8.0  day_aff=8.0 tick_aff=0.0   # 日上限 ±8 拦住
next-day 10368:    aff=10.0                            # 跨日重置
```

代码侧对应 `social.py` 的 `_clamp_delta()`：先算 `2.0 - tick_used` 与 `8.0 - day_used`
的余量，再把请求值夹进去；已存在关系写的是 `affection_delta`/`trust_delta`，
`effects.py` 做的是 `rel.affection + delta`，不是赋值覆盖。所以不存在"覆盖 bug"。

- 事件日志出现 `TalkAction` 的 effect：成立。
- 记忆 +2/轮：成立（本轮实测 4 条）。
- relation 不超 ±2/tick、±8/日：成立。
- 守恒仍 0：成立（账户总额 42,842 不变）。
- 回放 MATCH：成立。
- **但**：这些事件只写进 UI 的 `tempfile.mkdtemp` 临时 session（`atexit` 删除），
  原 `verify-72d` 的 12,777 条事件与 `51fb61e6…` 不变。交付说明 §6.8 已披露，
  验收时不能把 seq 12778/12779 当成"已写进交付 run"。
- 说明里的"R1 affection +2 / R2 0"依赖消息哈希（`_sentiment` 幅度取 1 或 2）；
  换一句话就会变成 +1/+0.5。机制正确，示例数值不应写成普遍结论。

---

## 4. 任务板与金库：数字成立，税差归因不成立

### 4.1 任务板

| 项 | 交付说明 | 实测 | 判定 |
|---|---|---|---|
| 板上过期 | 206 → 4 | P2 基线 **206**，P2.1 **4** | 一致 |
| 历史过期总数 | 75 | **75** | 一致 |
| 拒绝数 / 拒绝率 | 189 / 1.4792% | **189 / 1.4792%** | 一致 |
| 完成 / 累计失败 | 191 / 13 | **191 / 13**（各模板 failed 求和） | 一致 |
| 5 个 0 完成模板 | created=10 后停发 | 每个 **created=10 / expired=10 / completed=0** | 一致 |
| 各模板完成率 | 98.57 / 97.06 / 68.18 / 51.85 / 47.62 … | 逐项一致 | 一致 |

补充口径：P2.1 的 `quests.failed` **当前为 0**（失败单 7 天后被 prune），
13 是 `quest_stats` 里的累计失败；P2 没有 prune，所以它的 10 是"当前=累计"。
两者能对齐，但交付说明没写清这是"累计"。

### 4.2 金库

| 项 | 交付说明 | 实测 | 判定 |
|---|---|---|---|
| 金库期末 | 2,083 | **2,083** | 一致 |
| `treasury_guard` | 52/72 天，第 21 天进入 | **52 / 21** | 一致 |
| `open_shop_postponed` | 72 | **72** | 一致 |
| 交易税回流 | 86 | **86** | 一致 |
| 交易税 burn | 96 | **96** | 一致 |
| minted_total | 47,150 | **47,150** | 一致 |
| 守恒误差 | 0 | **0**（accounts_sum=42,842，53 账户） | 一致 |

### 4.3 "税 total 182 但回流 86" 的真实原因

逐事件解析 72d 的 `effects`：

```
transaction tax total = 182
treasury (transaction_tax_treasury transfer) = 86
burn (transaction_tax) = 96
burn 账户 == treasury:region 的事件数 = 0
75 笔交易；Σround(T/2) = 86；Σ(T/2) = 91.0
T 分布：1×(30), 3×(22), 2×(16), 8×(4), 9×(2), 4×(1)
其他 burn：income_tax 2249 / sect_registration 1500 / savings_tax 463
```

结论：

- 五条交易税路径（Buy / Sell / Trade / Consume / Escort）都走了 `tax_split_effects`，
  **没有漏改路径**；86 + 96 = 182，守恒没有损失。
- **"税源本身是 `treasury:region` 时自转折叠"在本 run 里一次都没发生**：
  所有 burn 的账户都是 NPC 钱包，`burn_from_treasury_events=0`。
- 86 与 91 的差是**逐笔 `round(amount * 0.5)` 的银行家舍入**：
  大量 1 文税被舍成 0 文入金库、整笔烧掉（T=1 出现 30 次）。
  这是分配偏差，不是"差额蒸发"，更不是漏路径。

因此第 4 条：**数字全部成立；交付说明给出的原因不成立** → 部分成立。

---

## 5. K1 为何下滑：数字成立，归因写错

用上一轮从 `delivery-p2` 干净重跑的 `runs/verify-delivery-p2/verify-72d` 做基线：

```
P2   : 71 / 91 = 78.02%   （content_hash 420ee2ff…，events 12,607）
P2.1 : 68 / 91 = 74.73%   （content_hash ff09d8a8…，events 12,777）
分母不变：91，目标 id 完全一致（91/91 相同）
```

逐目标 diff（`k1.rows`）：

| 目标 | 类型 | P2 | P2.1 |
|---|---|---|---|
| `luo_qingxu:goal_luo_qingxu_3` | build_shop | progress 1.0，completed | **0.0，active** |
| `ruan_xiaoluo:goal_ruan_xiaoluo_2` | build_shop | progress 1.0，completed | **0.0，active** |
| `ruan_xiaoluo:goal_ruan_xiaoluo_3` | build_relation | progress 1.0，completed | **not_created**（前序卡住） |

`progressed` 口径：**lost 3，gained 0**。所以 71 → 68 就是这三个目标。

真实原因不是任务板，而是 **`open_shop` 的金库缓冲**：

```bash
# 在新代码里回放到两次开店 tick
tick 2188 luo_qingxu : treasury=5450  wage_bill=759  7d=5313  buffer=7613  pass=False
tick 2724 ruan_xiaoluo: treasury=3563  wage_bill=759  7d=5313  buffer=7613  pass=False
```

- 缓冲公式：`2000 + 300 + 7 × 759 = 7,613`。
- 两次原本成功的开店（P2 的 seq 4303 / 5064）在 P2.1 直接没有生成 intent，
  计入 `postponed_open_shop`（共 72 次）。
- `build_shop` 不完成 → `_next_content_goal` 的轮转停在它上面（progress=0），
  同 NPC 的 `build_relation` 目标一直没被创建 → 第三个目标连带丢失。
- 这三个都不是任务模板目标，**任务板停发没有参与这次 K1 下滑**。

另外，"completed" 口径的 churn 比 K1 更大：5 个目标从 completed 变未完成
（`gu_qingya_3` 0.982、`wei_changgeng_1` 0.832、`luo_qingxu_3`、`ruan_xiaoluo_2`、
`ruan_xiaoluo_3`），3 个从未完成变 completed。前两个仍在 0.1 以上，所以只影响
"完成数"，不影响 K1 分子。

- 交付说明写的"原因是任务板收紧后完成类事件减少"：**不成立**。
- 这是修复（收入侧 `open_shop` 缓冲）带来的**真实副作用**，不是指标口径变化；
  但按 `docs/metrics.md v1.1.0` 的 K1 第 2.0 节，两个 `content_hash` 不同，
  跨版本差值本应标 `non_comparable`，交付说明却直接写"下降 3.29 点"。
  要么改成同内容包 A/B，要么明确标注不可比。

---

## 6. 总判定：有条件可交付

**可交付的部分**：code/内容/测试都从 tag 干净复现；回放、守恒、限幅、任务板清理、
金库分流都真实生效；`talk` 从"提案"变成了可回放的 Effect。没有发现覆盖 bug、
没有发现漏改的税路径、没有发现伪造数字。

**必须先改的三处（均为文档级，不需要改代码）**：

1. `reports/delivery-p2.1.md` §4：把"税源本身是 treasury:region → 自转折叠"改成
   "逐笔 50% 取整（银行家舍入），86/96 是分配结果，0 次金库自转，182 全额守恒"。
2. 同报告 §5/§6.2：把 K1 下滑归因从"任务板收紧"改为"`open_shop` 缓冲
   （7,613 门槛）压制 2 个 build_shop，并连带 1 个 build_relation 未创建"；
   同时标注 K1 跨 `content_hash` 按 v1.1.0 为 `non_comparable`。
3. 同报告 §3：把"完成 191 / 累计失败 13"注明 P2.1 的 13 是 `quest_stats` 累计、
   当前失败为 0；并把 talk 的 seq 12778/12779 注明为临时 session 证据、
   不写回交付 run。

次要：tag 未签名、tag 后是 2 个 docs commit；建议在交付说明中如实写明。

---

## 7. 一个玩家进来 30 分钟内会遇到的三个最难受的点

1. **说了话，世界不动，而且话会消失。**
   对话只写入临时 session（退出即删），原 run 的 12,777 条事件和哈希不变；
   同一 tick 连续两轮后好感/信任就吃满 ±2 上限，继续聊没有任何变化；
   NPC 回话是"人设卡台词 + 固定句式"，多聊几句就会听到重复句子。
   玩家会很快得出"这个对话是假的"。

2. **想开店基本开不了，经济是一潭死水。**
   金库期末 2,083，`treasury_guard` 52/72 天在线；
   `open_shop` 门槛是 `2000 + 300 + 7×759 = 7,613`，第 15 天金库只有 5,450、
   第 18 天只有 3,563，于是 72 次开店被静默延后，后 50 多天一个新铺子都开不出来。
   玩家的"攒钱 → 开店"主线在第一天之后就被系统悄悄掐断。

3. **任务板看得见、接得下、交不掉。**
   12 个模板里有 5 个 72 天 0 完成，各自发到 10 张就永久停发，板上还留着 4 张过期单；
   拒绝原因里 `not_adjacent` 62、`not_together` 55、`quest_not_accepted_by_actor` 11、
   `not_at_giver` 10——玩家最常遇到的是"你不在交单地点/没接这个单/不在一起"，
   但界面不会告诉他该去哪、该先接哪张。前 30 分钟很容易连续吃 3～4 次拒绝后放弃任务板。
