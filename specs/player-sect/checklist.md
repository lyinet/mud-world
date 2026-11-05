# player-sect 验收清单

## A. 设计规格

- [x] 立派门槛给出最终数字与依据（800 / 200 / 600 / 声望 20 / 单人）
- [x] 门派属性（名号、宗旨、驻地、等级、金库、职位）逐项给出落点与存储限制
- [x] 收徒条件（好感 30 / 信任 30 / 目标技能 0.2 / 掌门技能 0.4 / 声望 20）
- [x] 弟子每日行为（修炼 / 出任务 / 不在驻地 / 会费）
- [x] 弟子离开条件（欠饷 ≥ 30 文；声望下跌为 P2）
- [x] 职位与治理（掌门 P1；长老/执事权限表 + P2）
- [x] 钱流逐笔列出方向与 `reason`，无 `mint`
- [x] 守恒校验方式（`conservation()` + 金库余额恒等式 + 债务投影）
- [x] 与 `quests` / `business` / `intent.py` / `feed` 的接线设计
- [x] 原因码全表（中文文案，30 条）
- [x] 验收事件链（立派 → 收 2 徒 → 3 日 → 欠饷离派）含期望字段
- [x] P1/P2 分期，P2 只写目标

## B. 动作层（单测）

- [x] `PlayerFoundSectAction`：钱不足 / 声望不足 / 已在门派 / 重名 / id 已存在 / 非驻地 / 名字长度
- [x] `PlayerFoundSectAction`：成功时 200→官仓、600→金库、创始人入派、金库账户存在
- [x] `PlayerFoundSectAction`：NPC actor 委托原 `FoundSectAction`，行为不变
- [x] `PlayerRecruitDiscipleAction`：每个拒绝码至少一条（sect_not_found / not_sect_master / wrong_location / target_not_found / target_not_alive / self_recruit / target_is_player / not_together / target_in_sect / already_disciple / disciples_full / target_declined / skill_too_low）
- [x] `PlayerRecruitDiscipleAction`：成功后 `faction`、`master`、`disciples`、双向关系齐备
- [x] `PlayerPayDisciplesAction`：会费入账、俸禄出账、余额不足转债务、同日幂等、无弟子
- [x] `PlayerPayDisciplesAction`：空金库连续 3 日 → 欠饷 30 文 → 当日离派（faction/master=null、关系 ended、债务保留）
- [x] `PlayerExpelDiscipleAction`：拒绝码 + 成功清理
- [x] `PlayerSectDepositAction`：not_enough_money / bad_arg:amount
- [x] `PlayerSectWithdrawAction`：insufficient_treasury / withdraw_over_cap / treasury_reserve
- [x] 守恒：整链 `minted_total`、`burned_total` 不变，`conservation_error == 0`，无负余额
- [x] 确定性：同 state + intent 两次 `apply` 输出逐字节一致
- [x] 事件链原文可从 `Writer + EventStore` 读出（verb / args / effects 齐全）

## C. 接线前自检

- [x] 未修改 `actions/registry.py`、`actions/player.py`、`api/session.py`、`content/**`
- [x] 六个动作类可从 `wsim.actions.player_sect` 直接 import
- [x] 未新增 `mint` / `burn`；所有金额只经 `transfer` / `debt_*`
- [x] 提交只含 `specs/player-sect/**`、`src/wsim/actions/player_sect.py`、
      `tests/test_player_sect_actions.py`、`docs/player-api.md`、`CHANGELOG.md`
