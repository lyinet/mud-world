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
- [x] `PlayerRecruitDiscipleAction`：每个拒绝码至少一条（sect_not_found / no_sect / not_sect_leader / wrong_location / insufficient_rep / target_not_found / target_not_alive / self_recruit / target_is_player / not_together / target_in_sect / already_disciple / sect_full / favor_too_low / trust_too_low / skill_too_low）
- [x] `PlayerRecruitDiscipleAction`：成功后 `faction`、`master`、`disciples`、双向关系齐备
- [x] `PlayerPayDisciplesAction`：会费入账、俸禄出账、余额不足转债务、同日幂等、无弟子
- [x] `PlayerPayDisciplesAction`：空金库连续 3 日 → 欠饷 30 文 → 当日离派（faction/master=null、关系 ended、债务保留）
- [x] `PlayerExpelDiscipleAction`：拒绝码 + 成功清理（`disciple_not_found` / `target_not_disciple`）
- [x] `PlayerSectDepositAction`：insufficient_funds / bad_arg:amount
- [x] `PlayerSectWithdrawAction`：insufficient_treasury / withdraw_over_cap / treasury_reserve
- [x] 守恒：整链 `minted_total`、`burned_total` 不变，`conservation_error == 0`，无负余额
- [x] 确定性：同 state + intent 两次 `apply` 输出逐字节一致
- [x] 事件链原文可从 `Writer + EventStore` 读出（verb / args / effects 齐全）

## C. API 接线（2026-11-06 完成）

- [x] `WRITE_ACTIONS` 六个写动词 + `ACTION_ALIASES` 六个别名
- [x] `_args` 逐动作校验（`found_sect` 的 `name/at/doctrine/sect_id`、`recruit/expel` 的 `target`、
      `deposit/withdraw` 的 `amount`、`pay` 的 `day`；`sect_id` 可省略）
- [x] 六个动词进 `PLAYER_VERBS`，`found_sect` 移出 `PLAYER_UNIMPLEMENTED_VERBS`
- [x] 只经 `WorldSession` 注册；`found_sect` 对非玩家 actor 委托 NPC 动作
- [x] 私有 `player.sect` / 顶层 `sect`：门主、名册、金库、欠饷、`settled_today`
- [x] 匿名 `GET /api/player/state` 不含 `sect` / 弟子名单 / 金库；只有 `sect_name`
- [x] 写端点本人令牌；跨账号 403 `auth_account_mismatch`（HTTP 实测）
- [x] 拒绝码真实输出：`no_sect` / `already_in_sect` / `sect_full` / `insufficient_rep` /
      `insufficient_funds` / `disciple_not_found` / `favor_too_low` / `trust_too_low` /
      `not_sect_leader`（动作层）/ `arrears_too_high`（`settlement.leavers`）
- [x] 真实服务整链（立派 → 收徒 → 日结 → 存取 → 欠饷离派 → 驱逐）
- [x] 整链 `reconciliation.delta = 0`；金库存入/取出/俸禄全部真实 `transfer`
- [x] 无玩家世界 2 日 golden 与既有基线逐字节一致（`test_p3e_golden`）
- [x] `docs/player-api.md` §13 改为真实契约，拒绝码并入 §7
- [x] 提交只含 `src/wsim/actions/player_sect.py`、`src/wsim/actions/player.py`、
      `src/wsim/api/session.py`、`tests/test_player_sect_*`、`specs/player-sect/**`、
      `docs/player-api.md`、`CHANGELOG.md`
