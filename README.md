# world-sim — P1 无头世界审计器

独立 Python 项目，位于 `world-sim/` 子目录，**不修改仓库现有文件**。模拟是事件溯源架构：
SQLite `events` append-only 日志是唯一事实源，内存 `WorldState` 只是可重建的投影。

内容数据全部来自 `content/`（由另一会话维护，本目录只读）。运行模拟时
`allow_fixture=False`，内容缺失会直接报错；`fixtures/default/` 只在单元测试里使用。

指标口径固定在 [docs/metrics.md](docs/metrics.md)（v1.1.0，2026-11-03 生效，含 CHANGELOG）。

## 对比归因规则（强制）

`p2b-baseline` tag 冻结了 P2-B 交付快照（`3b1762e` / 代码 `42f4914`，
`content_hash=7acace8f2337…`）。**此后任何指标对比必须同时给出两边的 commit 与
content hash，否则视为不可归因**：

- K1 只允许同一快照内比较（同一 `content_hash`、同一目标集、同一 seed、同一天数）；
  跨内容版本一律标记 `non_comparable`，summary 输出 `k1.comparison_basis`。
- K3 对比必须注明两边使用的签名 spec 版本（v1.0.0 / v1.1.0），同一 run 同时输出两版。
- 只给百分比、不给 commit 与 content hash 的数字，不得写进结论。

## 安装

```bash
cd world-sim
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 运行

```bash
# 10 个游戏日，30 NPC
.venv/bin/python sim/run_headless.py --days 10 --seed 42 --npc 0 --run-id rev10d

# 72 个游戏日，30 NPC
.venv/bin/python sim/run_headless.py --days 72 --seed 42 --npc 0 --run-id rev72d

# 同一 seed 双跑复现
.venv/bin/python sim/run_headless.py --days 10 --seed 42 --npc 0 --run-id rev10d-repro
diff runs/rev10d/world_hash.txt runs/rev10d-repro/world_hash.txt && echo SAME

# 从事件日志回放重建，验证投影 == 日志
.venv/bin/python tools/replay.py --run runs/rev10d   # 期望 MATCH
.venv/bin/python tools/replay.py --run runs/rev72d   # 期望 MATCH

# 测试
.venv/bin/python -m pytest
```

`--npc 0` 表示使用全部 NPC；`--dump-state` 会额外写出 `state_final.json`（大文件，调试用）。
本轮运行对应的内容包 hash 是 `b639ab91a99918b4297b034617b94cc67da7591255c695f97ec48f13a5edf3d4`
（`content/` 已由另一会话更新：补了 `home` / `goal_targets`，删除了 `cooldown`）。
**本轮数字不能与更新前的内容包直接比较。**

## 本次实跑结果（seed=42，30 NPC，单区域）

| 指标 | 10 日 `rev10d` | 72 日 `rev72d` |
|---|---|---|
| state_final | `b136b459b0807ff15edbd8c3f1b0baf33ebcf679d3289d1025d0ad85362052c4` | `e4254a504ff75bc6467022f368e6b9c4ecc92a9a5a487fcead4a3cbf43fbb606` |
| state_day10 | 同左 | `b136b459b0807ff15edbd8c3f1b0baf33ebcf679d3289d1025d0ad85362052c4` |
| event_final | `5d82f297adec9bca73d24fcff034f9d3a6696d80f85bd63500aca82daf870284` | `12d9b93ff9d4e1e06b57635e1ea4dd1b85cfc373a355a74d0a071799b6c821cc` |
| 事件数 / 拒绝率 | 2877 / 9（0.3128%） | 19322 / 64（0.3312%） |
| **K6 守恒误差** | **0** | **0** |
| K6 负账户 / 债务独立审计 | 无 / **PASS** | 无 / **PASS** |
| **K1 全量口径（分母 90）** | **38/90 = 42.22%（不达标）** | **68/90 = 75.56%（达标）** |
| K1 已创建口径（仅参考） | 38/42 = 90.48% | 68/71 = 95.77% |
| K1 剔除一次性目标 | 35.00% | 67.16% |
| 一次性目标占已推进 | 10/38 = 26.32% | 23/68 = 33.82% |
| K3 | 诊断，不判定 | 诊断，不判定 |
| K3 verb 层 Top-10 / 熵 | 0.9982 / 2.3252 bits | 0.9989 / 2.0702 bits |
| K3 对象层 Top-10 / 熵 | 0.6665 / 4.7919 bits（94 签名） | 0.7211 / 4.4235 bits（99 签名） |
| K3 对象层 NPC 归一化熵 均值/最小 | 0.8785 / 0.8184 | 0.7833 / 0.6519 |
| K3 对象层 NPC 两两 JS 均值 | 0.4651 | 0.4098 |
| K3 折叠 move 后 Top-10 / 熵 | 0.7735 / 3.9020 bits | **0.8349 / 3.4462 bits** |
| K3 折叠 move 后 NPC 熵 均值/最小 | 0.7895 / 0.6937 | **0.6799 / 0.5432** |
| K3 折叠 move 后两两 JS | 0.3685 | 0.3036 |
| NPC 现金变化/日 | -6.29 | +1.39 |
| NPC 经营净现金流/日 | +6.71 | +5.10 |
| NPC 净资产变化/日 | +6.71 | +4.86 |
| 内容包理想值/日 | 5.88 | 5.88 |
| 工资应发 / 实付 / 欠薪 | 7370 / 7370 / 0 | 53064 / 50469 / 2595 |
| 货币总量 minted / burned | 47150 / 654 | 47150 / 4852 |

结论：

- **K6 通过**：守恒误差 0；债务从事件日志独立重建，9 项检查全部通过。
- **K1 72 日通过（75.56%），10 日不通过（42.22%）**。10 日只有 42/90 个目标被创建，
  全量分母下必然低；10 日不是 K1 的评估期。72 日剔除一次性目标后仍有 67.16%。
- **K3 不判定**。按 P1 口径只输出诊断数值。若强行套用已废止的 v2 阈值，
  对象层 6/6 通过，但把 move 折叠后 72 日是 3/6 不通过（Top-10 >0.80、熵 <4.0、
  NPC 熵均值 <0.70）。这说明 v2 的“通过”确实部分来自把 move 按地点拆桶。
- 同 seed 双跑（`rev10d` / `rev10d-repro`）状态与事件哈希完全一致；10 日、72 日回放均 MATCH。

### 为什么 10 日经营净现金流 6.71 高于理想值 5.88？

10 日是短周期，且本轮内容包补了 `home` 后行为路径变化。现金流里有内容包理想模型之外的
两类收入：起步库存卖出净额（sell 919 − buy 835 = 84 文）和押镖分成 504 文；同时没有欠薪。
放到 72 日，经营净现金流回落到 5.10，低于理想值 5.88。两轮 `minted` 都固定为 47150、
K6 误差为 0，没有日常增发。10 日的 6.71 仍在内容包给出的 1.4–10.4 区间内，但高于其
中心值 5.88；这是短周期与额外收入，不是铸币。

### 为什么 NPC 现金变化与经营净现金流不同？

开店铺/建门派的支出是资本开支，不是经营亏损：

- 10 日现金 −1887，资本开支 3900（店铺租金），经营净现金流 +2013。
- 72 日现金 +3008，资本开支 8000（7500 店铺租金 + 500 建派注册费 burn），
  经营净现金流 +11008；净资产 +10508（店铺账面 7500 计入资产，500 注册费
  不可资本化，差额即此）。

## K1 全量口径与 22 个未达标目标（72 日）

分母是 `content/npcs.yaml` 的 90 个目标，未创建即记未推进。72 日实测：

- 已创建 71，已推进 68；未创建 19，已创建但未推进 3。
- **未创建 19 个的阻塞原因**：
  - 15 个卡在前序 `advance_skill` 未完成（进度 0.275–0.783，72 日内未到目标）；
  - 4 个卡在 `found_sect`（谢停云 1 个下游、燕守一 2 个下游、白露生 1 个下游；
    白露生的 `found_sect` 到第 68.65 日才成功，下游目标来不及创建）。
- **已创建但未推进 3 个**：谢停云 `found_sect`（成员不足）、燕守一 `found_sect`（成员不足）、
  白露生 `advance_skill`（第 69 日才创建，期末进度 0）。
- `found_sect` 已按内容包 `min_members: 5` 执行。P1 把“创始成员”定义为
  创始人 + 在世弟子 + 亲密度 ≥ `min_relation_value`（30）的 friend/family/ally，
  且不在其他 `sect:`。72 日内只有白露生在第 68.65 日凑齐 11 人并建派；
  谢停云、燕守一始终不足 5 人，因此这两个目标失败。
- 一次性目标（一次动作即完成）：23 个，占已推进的 33.82%，按类型为
  `gain_status` 12、`build_shop` 10、`found_sect` 1。
  它们把 K1 从 67.16%（剔除一次性）抬到 75.56%，抬升 8.40 个百分点。

**与复核报告 84/90 的差异**：84/90 是基于旧内容包和旧 `found_sect` 逻辑的预期。
本轮内容包 hash 已变（补 `home` / `goal_targets`），且 `min_members: 5` 开始生效，
只有 1/3 的建派目标成功，连带 3 个下游目标无法创建；另有 15 个目标卡在 `advance_skill`
的未完成进度。实测就是 68/90，不是 84/90。

## K3 双层诊断（P1 不做 pass/fail）

完整口径见 [docs/metrics.md](docs/metrics.md)。签名 v1.0.0 =
`verb + 参数语义 + target 类型`，不含 NPC id、数量、时间、金额；对象 id 来自内容包。
同时输出 verb 层、对象层、move 折叠视图，避免只报对自己有利的一层。

72 日 verb 层计数：

```
move 5809  consume 4641  work 2160  cultivate 1460  trade 522  sell 73  buy 50
escort 28  join_sect 12  open_shop 10  apprentice 9  marry 6  found_sect 1
```

- verb 层 Top-10 = 0.9989，熵 = 2.0702 bits，每 NPC 独立 verb 最少 4。
- 对象层 Top-10 = 0.7211，熵 = 4.4235 bits（有效模式 21.46），
  每 NPC 归一化熵均值 0.7833 / 最小 0.6519，两两 JS 均值 0.4098，
  零假设 JS 0.0526，信噪比 7.79×。
- **把 `move:<地点>` 折叠为 `move:any` 后**：Top-10 = 0.8349，熵 = 3.4462 bits，
  NPC 熵均值 0.6799 / 最小 0.5432，两两 JS 0.3036，独立 verb 最少 6。

按已废止的 v2 阈值对照（只作历史参照，不判定）：

| 判据 | 不折叠 | 折叠 move | v2 阈值 |
|---|---|---|---|
| Top-10 | 0.7211 | **0.8349** | <0.80（折叠后失败） |
| 签名熵 | 4.4235 | **3.4462** | ≥4.0（失败） |
| NPC 熵均值 | 0.7833 | **0.6799** | ≥0.70（失败） |
| NPC 熵最小 | 0.6519 | 0.5432 | ≥0.50（通过） |
| 两两 JS | 0.4098 | 0.3036 | ≥0.30（通过） |
| 独立 verb 最小 | 8 | 6 | ≥3（通过） |

所以：不折叠时 v2 六项全过；折叠 move 后 **3/6 不达标**。复核报告写的是 5/6，
差异来自内容包与运行变化，但方向一致——v2 的通过混入了拆桶红利。因此 v1.0.0 取消
K3 pass/fail，v2 阈值标记为 `revoked`。

## K6 债务独立审计（不再是恒真式）

旧实现只验证“应收合计 == 应付合计”，而两者由同一个 `amount` 同时加减，永远成立。
本轮改为 `audit/debt_audit.py` 从事件日志独立重建：

1. 逐事件累加 `transfer` / `mint` / `burn` / `debt_settle` 的实际账户变动；
2. 每笔 `debt_settle` 校验应收方增加 == 应付方减少 == 债务金额（并检查付款方余额）；
3. 每笔债务金额与来源独立核对：
   `initial_debt` ↔ 内容包关系金额；`unpaid_wage` ↔ 同事件应发 − 实付；
   `living_*` ↔ 生活成本 − 实付；`escort_fee` ↔ 镖单分成 − 实付；
4. 核对“声明 − 已结算 == 期末状态金额”；
5. 逐事件复式平衡：正变动 + 销毁 == 负变动 + 发行；
6. 任何“钱包增加但付款方未减少”直接失败；genesis 之外出现 mint 直接失败。

72 日实测：118 笔债务、声明总额 3835 文（初始 1230 + 工资欠条 2595 + 押镖欠款 10），
已结算 0，全部 9 项检查通过；重建账户合计 42298 == 状态账户合计 42298。
逐笔工资明细见 `runs/rev72d/wage_trace.json`。

## 工资链路

| | 10 日 | 72 日 |
|---|---|---|
| 应发 gross | 7370 | 53064 |
| 实付 paid | 7370 | 50469 |
| 新建欠薪 | 0 | 2595 |
| 已清偿 | 0 | 0 |
| 存续 IOU | 0 | 105 笔 2595 文 |
| 对账 5 项 | 全 true | 全 true |

实付转账 reason=`wage`，未付欠账 note=`unpaid_wage`；恒等式 `gross == paid + unpaid`
逐笔成立。实付与现金流 `inflow_by_reason.wage` 一致（10 日 7370，72 日 50469）。

## 与内容包的字段对齐

| 字段 | 本轮内容包 | 引擎处理 |
|---|---|---|
| `npcs.yaml home` | 30/30 已声明 | 直接读取；缺失时才用工作地点相邻 + id 哈希，并在 `content_audit.defaults_used` 标记 |
| `economy.yaml goal_targets` | bond/faction/mastery/shop/wealth 全部声明 | 直接读取；缺失 kind 时才用默认 target，并标记 |
| `verbs.yaml cooldown` | 字段已删除（`cooldown_field_present=false`） | 不读取、不实现、不假装节流 |
| `relations.long_term` | 当前 0 条 | 引擎支持；补数据后 72 日内不强制清偿但进账本 |
| `found_sect min_members` | 5 | 已强制执行，3 个建派目标中 1 个成功 |
| 债务 `amount` | 24 条 creditor/debtor 关系全部带 amount | 双方各一条分录，金额独立核对 |

`content_audit.issues` 本轮为空；`defaults_used.home.defaulted = 0`，
`defaults_used.goal_targets.defaulted_goals = 0`。

## 未达标 / 尚未解决

1. **K1 10 日 42.22% 不达标**：全量分母 90，但 10 日只创建 42 个目标。P1 的评估期是 72 日；
   若要求 10 日也 ≥60%，需要内容包把目标链压短，而不是把分母改回已创建目标。
2. **K1 72 日 75.56%，但依赖一次性目标 8.40 个百分点**：剔除一次性后 67.16%，
   仍过 60%，但余量只有 7.16 个百分点。`advance_skill` 的 15 个阻塞目标是主要拖累。
3. **K3 verb 层集中**：move/consume/work 占 88.9%（72 日），verb 熵仅 2.07 bits。
   这是 P1 日程驱动的预期现象，不是靠改签名能解决的；需要 P3 意图层。
4. **K3 折叠 move 后 3/6 v2 判据不达标**：Top-10、签名熵、NPC 熵均值。该结果如实保留，
   不调粒度、不恢复阈值。
5. **2 个 found_sect 目标失败**：谢停云、燕守一在 72 日内凑不到 `min_members: 5`。
   这是内容包数值与 P1 无招人动作的结构性缺口，不是把门槛改小能解决的。
6. **72 日仍有 105 笔 2595 文工资欠条**：NPC 钱包 38658 文，商业体现金仅 520 文；
   总货币量固定下的流动性问题，IOU 已进账本、可清算，不是增发。
7. **拒绝 64 笔（0.3312%）**：25 item_not_available、11 not_together、11 债务人余额不足、
   8 not_adjacent、7 镖局现金不足、2 already_married；低于 1% 阈值。
8. **found_sect 建派时一次性拉入 11 名成员**：这是按“可召集成员”定义实现的；
   P2 若要做真正的招人流程，需要独立动作与成员准入，不应继续用建派事件批量拉人。

## 目录结构

```
world-sim/
├── content/                 # 只读：NPC/地点/物品/动词/经济参数（另一会话维护）
├── config/role_workplace.yaml
├── docs/metrics.md          # 指标口径 v1.0.0 + CHANGELOG
├── src/wsim/
│   ├── core/                # canonical JSON/hash、虚拟时钟、PRNG、内容加载、模型
│   ├── world/               # WorldState、Effect 应用、唯一写者、事件存储、哈希
│   ├── actions/             # 13 个 NPC 动词 + system_* 系统动作（纯 apply）
│   ├── sim/                 # genesis 创世、headless 主循环
│   └── audit/               # K6/K1/K3、现金流、工资链路、债务独立审计、内容对齐
├── sim/run_headless.py      # CLI
├── tools/replay.py          # 事件日志回放校验
├── tests/                   # 守恒、原子性、纯洁性、复现、回放、K3、债务审计、确定性 lint
├── fixtures/default/        # 仅测试用最小 fixture（运行时不使用）
└── runs/                    # 实跑输出：rev10d / rev10d-repro / rev72d（另有历史 run）
```
