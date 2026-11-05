# player-sect 任务分解

## P1 动作层（本笔实现）

| # | 任务 | 产物 | 验收 |
|---|---|---|---|
| 1 | 立派动作 | `src/wsim/actions/player_sect.py::PlayerFoundSectAction` | 800 文拆成 200 注册费（→官仓）+ 600 启动金（→门派金库）；声望 20；已有 `sect:` 拒绝；NPC 调用走原 `FoundSectAction` 分支 |
| 2 | 收徒动作 | `PlayerRecruitDiscipleAction` | 好感/信任 30、目标技能 0.2、掌门技能 0.4、名册 <3；拒绝码 `target_declined` / `disciples_full` 等 |
| 3 | 逐出动作 | `PlayerExpelDiscipleAction` | 只有掌门、只逐本门弟子；清理 `faction` / `master` / 双向关系 |
| 4 | 门派日结 | `PlayerPayDisciplesAction` | 会费真实入账；俸禄真实出账；不足转债务；欠饷 ≥30 文当日离派；同日幂等 |
| 5 | 存入/提取 | `PlayerSectDepositAction` / `PlayerSectWithdrawAction` | 提取单次 ≤500、留 100 保底；全部真实 `transfer` |
| 6 | 单测 | `tests/test_player_sect_actions.py` | 门槛、拒绝码逐条、守恒、事件链、确定性 |

## 接线（不属于本笔提交，由主线会话执行）

动作类可导入即用；下面按文件给出插入点（行号为当前工作区的近似位置，落笔时以符号名为准）。

### 1. `src/wsim/actions/registry.py`

- 第 44 行 `from .business import FoundSectAction, OpenShopAction` 之后新增：
  ```python
  from .player_sect import (
      PlayerExpelDiscipleAction,
      PlayerFoundSectAction,
      PlayerPayDisciplesAction,
      PlayerRecruitDiscipleAction,
      PlayerSectDepositAction,
      PlayerSectWithdrawAction,
  )
  ```
- 第 106 行把 `reg.register(FoundSectAction(content))` 替换为
  `reg.register(PlayerFoundSectAction(content))`（玩家分支是新增语义，非玩家分支原样委托给
  `FoundSectAction`，NPC 行为逐字节不变）。
- 在第 106 行之后追加：
  ```python
  reg.register(PlayerRecruitDiscipleAction())
  reg.register(PlayerExpelDiscipleAction())
  reg.register(PlayerPayDisciplesAction())
  reg.register(PlayerSectDepositAction())
  reg.register(PlayerSectWithdrawAction())
  ```

### 2. `src/wsim/actions/player.py`

- 第 45–72 行 `PLAYER_VERBS` 增加：
  `"found_sect"`、`"recruit_disciple"`、`"expel_disciple"`、`"pay_disciples"`、
  `"sect_deposit"`、`"sect_withdraw"`。

### 3. `src/wsim/api/session.py`

- 第 142 行 `PLAYER_UNIMPLEMENTED_VERBS` 删除 `"found_sect"`（保留 `marry` / `apprentice`）。
- 第 78–108 行 `WRITE_ACTIONS` 增加六个客户端动词 → 内部 verb 的映射（同名即可）。
- 第 111–139 行 `ACTION_ALIASES` 同步增加六个动词。
- 第 1590 行 `_args` 增加六个分支：
  - `found_sect`：`name`、`at`（缺省实体所在地）、`doctrine`、`sect_id`
  - `recruit_disciple` / `expel_disciple`：`sect_id`、`target`
  - `pay_disciples`：`sect_id`、`day`（缺省由动作层算）
  - `sect_deposit` / `sect_withdraw`：`sect_id`、`amount`
- 第 164–264 行 `REASON_TEXT` 追加 `spec.md §6` 的 30 条中文文案。
- 第 698–701 行 `_feed_summary` 增加 `found_sect` / `recruit_disciple` / `expel_disciple` /
  `pay_disciples` 的中文摘要分支。
- 第 620–666 行 `world_feed`：当前 SQL 与循环排除 `player:%`；门派事件要进公共动态，
  需单独放行 `verb in SECTION_FEED_VERBS` 的玩家事件（只输出摘要，不带金额/成员细节）。

### 4. `src/wsim/sim/intent.py`（P1 之外的 NPC 侧）

- 第 60、74–75 行动作标签：`found_sect` / `join_sect` 已有，可补 `recruit_disciple` / `pay_disciples`。
- 第 1156 行 `_recruit_target` 之后增加弟子日常分支：在驻地修炼、外出跑委托、日界回驻地。
- 第 1313 行 `gain_status` / `join_sect` 分支之后，增加「已有门派且为掌门」的日结调度
  （建议由 `system_*` 日界动作统一触发，避免玩家离线被扣款）。

## P2（只写目标，不实现）

| # | 目标 | 关键设计 |
|---|---|---|
| 1 | 门派产业 | 开店 `owner=<fid>`、`cash_account=treasury:sect:<sid>`；营收/进货走真实 `transfer`；日结并入 `pay_disciples` |
| 2 | 门派等级 | 1→2：500 文（金库→官仓）+ 6 弟子；2→3：1500 文 + 9 弟子；新增 `upgrade_sect` 动作 |
| 3 | 门派专属委托 | `content/quests.yaml` 新增 `sect_*` 模板；奖励从金库出；`sect_reputation` 计入声望 |
| 4 | 门派声望 | 派生指标（成员好感 + 委托 + 金库），低于 10 连续 5 日触发弟子流失 |
| 5 | 门派间关系 | `sect:*` ↔ `sect:*` 的 `ally` / `rival` 关系；冲突以委托/押镖风险表达，不做战斗 |
| 6 | 长老/执事任命 | `appoint_office` 动作；需 `Faction.offices` 字段（P2 才允许改 `state.py`） |
