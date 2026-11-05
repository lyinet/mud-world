# player-sect 任务分解

## P1 动作层（已完成）

| # | 任务 | 产物 | 验收 |
|---|---|---|---|
| 1 | 立派动作 | `src/wsim/actions/player_sect.py::PlayerFoundSectAction` | 800 文拆成 200 注册费（→官仓）+ 600 启动金（→门派金库）；声望 20；已有 `sect:` 拒 `already_in_sect`；钱/声望不足 `insufficient_funds` / `insufficient_rep`；NPC 调用走原 `FoundSectAction` 分支 |
| 2 | 收徒动作 | `PlayerRecruitDiscipleAction` | 好感/信任 30、目标技能 0.2、掌门技能 0.4、名册 <3；拒绝码 `favor_too_low` / `trust_too_low` / `skill_too_low` / `sect_full` 等 |
| 3 | 逐出动作 | `PlayerExpelDiscipleAction` | 只有掌门、只逐本门弟子；`disciple_not_found` / `target_not_disciple`；清理 `faction` / `master` / 双向关系 |
| 4 | 门派日结 | `PlayerPayDisciplesAction` | 会费真实入账；俸禄真实出账；不足转债务；欠饷 ≥30 文当日离派；同日幂等 |
| 5 | 存入/提取 | `PlayerSectDepositAction` / `PlayerSectWithdrawAction` | 提取单次 ≤500、留 100 保底；全部真实 `transfer` |
| 6 | 单测 | `tests/test_player_sect_actions.py` | 门槛、拒绝码逐条、守恒、事件链、确定性 |

## API 接线（2026-11-06 已完成）

实际落点与最初草案不同：门派动作**不进 `actions/registry.py`**，只在 `WorldSession` 注册，
以保证无玩家世界的确定性；`found_sect` 对非玩家 actor 委托 NPC 动作。

| # | 文件 | 完成内容 |
|---|---|---|
| 1 | `src/wsim/actions/player.py` | `PLAYER_VERBS` 追加六个门派动词 |
| 2 | `src/wsim/api/session.py` | ① `WorldSession.__init__` 注册六个 `Player*Action`；② `WRITE_ACTIONS` + `ACTION_ALIASES` 六个映射；③ `PLAYER_UNIMPLEMENTED_VERBS` 移除 `found_sect`；④ `REASON_TEXT` 门派码中文；⑤ `_args` 逐动作校验（`sect_id` 可省略）；⑥ `_sect_view` 私有快照 + `player.sect` / 顶层 `sect`；⑦ `_response` 追加 `details.sect` 与 `details.settlement` |
| 3 | `tests/test_player_sect_api.py` | 真实 HTTP：本人令牌 / 跨账号 403 / 匿名边界 / 拒绝码 / 立派收徒日结整链 / 守恒 |
| 4 | `docs/player-api.md` §13 | 对外契约与拒绝码全表；§3.3 私有状态；§7 错误码 |
| 5 | `specs/player-sect/checklist.md` | B/C 两节逐项勾选 |

## NPC 侧（P1 之外，未做）

- `sim/intent.py` 弟子日常分支（驻地修炼 / 外出 / 日界回驻地），见 spec §5。
- 日界系统动作统一触发门派日结，避免玩家离线被扣款。
- 公共 feed：本轮不开放玩家门派事件（私有状态）；如要公开，只放名号摘要。

## P2（只写目标，不实现）

| # | 目标 | 关键设计 |
|---|---|---|
| 1 | 门派产业 | 开店 `owner=<fid>`、`cash_account=treasury:sect:<sid>`；营收/进货走真实 `transfer`；日结并入 `pay_disciples` |
| 2 | 门派等级 | 1→2：500 文（金库→官仓）+ 6 弟子；2→3：1500 文 + 9 弟子；新增 `upgrade_sect` 动作 |
| 3 | 门派专属委托 | `content/quests.yaml` 新增 `sect_*` 模板；奖励从金库出；`sect_reputation` 计入声望 |
| 4 | 门派声望 | 派生指标（成员好感 + 委托 + 金库），低于 10 连续 5 日触发弟子流失 |
| 5 | 门派间关系 | `sect:*` ↔ `sect:*` 的 `ally` / `rival` 关系；冲突以委托/押镖风险表达，不做战斗 |
| 6 | 长老/执事任命 | `appoint_office` 动作；需 `Faction.offices` 字段（P2 才允许改 `state.py`） |
