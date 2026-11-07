# xkx 帮助与命令覆盖矩阵

> 由 `tools/xkx_help/build.py` 生成，请勿手改。
> 源：`fluffos/xkx100@c1b87db0014c85514e053b2a149a2efccb9914e3`；命令口径 153 条，主题 32 个。

## 一、口径

- **已实现**：玩家当前可以通过引擎动作或只读视图获得该命令的核心效果；局部缺失写在依据里。
- **待实现**：效果可以映射到现有模型，但命令入口或必要字段尚未接入。
- **不做**：当前架构不建模，或产品明确排除；不进入排期。
- 中文别名是复刻版文字壳使用的命令名；xkx 原版使用英文动词，别名列为人工对照，不是源码直译。
- 原版帮助主题在源仓库中只有 32 条标题索引，没有逐主题正文文件；主题正文按标题与命令源码重写。

## 二、总览

| 状态 | 数量 | 占比 |
| --- | ---: | ---: |
| 已实现 | 40 | 26.1% |
| 待实现 | 51 | 33.3% |
| 不做 | 62 | 40.5% |
| 合计 | 153 | 100.0% |

| 归属系统 | 已实现 | 待实现 | 不做 | 合计 |
| --- | ---: | ---: | ---: | ---: |
| 移动 | 2 | 0 | 0 | 2 |
| 战斗 | 2 | 13 | 5 | 20 |
| 物品 | 10 | 2 | 7 | 19 |
| 社交 | 4 | 5 | 20 | 29 |
| 门派 | 6 | 23 | 4 | 33 |
| 经济 | 4 | 0 | 2 | 6 |
| 系统 | 12 | 8 | 24 | 44 |

## 三、逐条覆盖矩阵

| # | 命令 | 中文别名 | 参数 | 一句话说明 | 归属系统 | 状态 | 依据 |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| 1 | `ansuan` | 暗算 | <人物> at <方向> | 对指定方向上的目标暗中下手，命中后造成额外伤害。 | 战斗 | **待实现** | 基础回合战斗已由 FightAction（src/wsim/actions/combat.py）提供；暗算还缺潜行/先手判定。 |
| 2 | `ask` | 问、打听 | <某人> about <话题> | 向 NPC 打听或自由提问，按人物设定给出回应。 | 社交 | **已实现** | TalkAction（src/wsim/actions/social.py）已注册，文字壳已接「问/打听」，可自由交谈；尚无 xkx 话题表。 |
| 3 | `attack` | 唆使 | <动物> | 命令已驯服的动物攻击指定目标。 | 战斗 | **不做** | 宠物与坐骑实体、驯服度均未建模；本版不开放驯兽与宠物参战。 |
| 4 | `baitan` | 摆摊、出摊 | — | 在所在位置摆摊，向路过的玩家出售货物。 | 经济 | **已实现** | OpenShopAction（src/wsim/actions/business.py）支持摊位型店铺；RestockAction/SetPriceAction 管库存与定价。 |
| 5 | `beg` | 乞讨 | <物品> from <某人> | 向他人乞讨物品或钱财。 | 社交 | **不做** | 乞讨会绕过交易与劳动产出，产品上不开放。 |
| 6 | `bugs` | 报错 | — | 向巫师报告游戏错误。 | 系统 | **不做** | 本版用仓库 Issue 跟踪缺陷，不提供游戏内报错入口。 |
| 7 | `buy` | 买、购买 | <物品> [from <玩家>] | 从店铺或玩家处买下物品并支付价款。 | 经济 | **已实现** | BuyAction（src/wsim/actions/commerce.py）；价格与库存由店铺状态校验。 |
| 8 | `ce` | 私语 | [kiss\|bye] [讯息] | 与配偶进行只有两人可见的悄悄话。 | 社交 | **不做** | 玩家间私聊频道未建模；婚姻系统只记录配偶关系。 |
| 9 | `check` | 探察、打探 | [<某人>] | 查看某人（或自己）的武功与技能等级。 | 社交 | **已实现** | session._present_view 与 _skills_detail（src/wsim/api/session.py）给出在场人物技能明细；暂不支持查离线玩家。 |
| 10 | `chi` | 吃（同 eat） | <物品> | 吃下食物，恢复体力。 | 物品 | **已实现** | eat 的别名；ConsumeAction（src/wsim/actions/basic.py）结算消耗与体力恢复。 |
| 11 | `close` | 关门 | <门> 或 <方向> | 关闭一扇门或某个方向的出入口。 | 物品 | **不做** | 门与容器未建模；本版房间不设可开关门。 |
| 12 | `come` | 唤宠 | <动物> | 让已驯服的动物跟随自己行动。 | 系统 | **不做** | 宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。 |
| 13 | `cuifa` | 催发 | <人物> | 催发三尸脑神丹，控制服丹者。 | 门派 | **不做** | 门派控制类毒药与生死控制未建模，且属于 PK 向玩法，本版不开放。 |
| 14 | `dating` | 探察、打探（同 check） | [<某人>] | 查看某人的武功与技能等级。 | 社交 | **已实现** | check 的别名；复用 _present_view/_skills_detail（src/wsim/api/session.py）。 |
| 15 | `demote` | 查表情 | — | 列出某个表情动作的全部变化形式。 | 社交 | **不做** | 表情系统未建模；文字壳不提供 emote 变体查询。 |
| 16 | `drink` | 喝、饮用 | [<饮品>] | 喝下饮品，恢复体力或解除部分状态。 | 物品 | **已实现** | he 的等价命令；ConsumeAction（src/wsim/actions/basic.py）与 UseItemAction（src/wsim/actions/equipment.py）。 |
| 17 | `drop` | 丢下、扔掉 | <物品> [all] | 把物品放到当前房间的地面上。 | 物品 | **不做** | 地面物品与无主物品模型未建模；V1 明确不做地面掉落。 |
| 18 | `eat` | 吃、进食 | <物品> | 吃下食物，恢复体力。 | 物品 | **已实现** | ConsumeAction（src/wsim/actions/basic.py）；食物耗尽后从背包移除。 |
| 19 | `emote` | 表情 | <动作词> | 做一个表情动作，让同房间的人看到。 | 社交 | **不做** | 玩家间表情广播未建模；本版只保留 NPC 交谈。 |
| 20 | `feed` | 喂养 | <饲料> to <动物> | 用饲料喂养自己的动物。 | 系统 | **不做** | 宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。 |
| 21 | `femote` | 找表情 | [-s] <关键字> | 检索符合关键字的感情动作。 | 社交 | **不做** | 玩家表情动作与广播未建模；本版不开放表情检索。 |
| 22 | `fight` | 切磋、较量 | <人物> | 向目标发起战斗，按回合结算招式、伤害与胜负。 | 战斗 | **已实现** | FightAction（src/wsim/actions/combat.py）已注册，文字壳的 fight/打/战斗/出手 可直接调用；单场最多 20 回合。 |
| 23 | `follow` | 跟随 | [<生物>\|none] | 跟随某个生物行动，或取消跟随。 | 社交 | **不做** | 玩家/NPC 跟随关系未建模，V1 只支持固定房间移动。 |
| 24 | `gamble` | 赌博 | <大\|小> <钱> <种类> <数量> | 在赌场押注，按骰子结果结算输赢。 | 经济 | **不做** | 赌博属产品明确排除内容，且会破坏经济模型。 |
| 25 | `get` | 拿、捡 | <物品> [from <容器>] | 从地面或容器中取走物品。 | 物品 | **待实现** | 战斗掉落只产出 loot_create 效果（src/wsim/world/effects.py:104 定义、:422 处理），src/wsim/actions/combat.py:124 在战斗结算里发出该效果，但没有 LootAction 消费它；地面物品模型也未建模。 |
| 26 | `give` | 给、赠送 | <物品> to <某人> | 把物品无偿转交给他人。 | 物品 | **待实现** | 现有 TradeAction 只支持双方对价交易，没有单向转移动作；可复用装备/背包模型后补。 |
| 27 | `go` | 走、前往 | <方向> | 沿指定方向移动到相邻房间。 | 移动 | **已实现** | MoveAction（src/wsim/actions/basic.py）；出口校验见 session._location_view。 |
| 28 | `guard` | 护卫 | [<某人>\|<物品>\|<出口>\|cancel] | 为某人、某物或某个出口担任护卫。 | 战斗 | **不做** | 护卫/拦截玩法依赖战斗与实时对抗，本版不开放。 |
| 29 | `halt` | 停手 | — | 中止当前正在进行的动作。 | 系统 | **不做** | 本版动作按事件原子结算，没有可中断的忙碌状态。 |
| 30 | `han` | 喊话、大喊 | <讯息> | 向所在区域大喊，让附近的人听到。 | 社交 | **不做** | 区域广播/频道未建模。 |
| 31 | `hbless` | 贺婚 | <男名> with <女名> | 向新婚夫妇道贺。 | 社交 | **不做** | 婚礼仪式与广播未建模；婚姻只用 MarriageAction 记录关系。 |
| 32 | `he` | 喝（同 drink） | [<饮品>] | 喝下饮品，恢复体力。 | 物品 | **已实现** | drink 的别名；ConsumeAction（src/wsim/actions/basic.py）。 |
| 33 | `hit` | 出手、进招 | <人物> | 对目标发出一击，不做完整战斗结算。 | 战斗 | **待实现** | FightAction（src/wsim/actions/combat.py）已有完整回合战斗；hit 需要单次攻击接口，文字壳尚未挂动词。 |
| 34 | `imbibe` | 喂饮 | <动物> | 给动物喂水，维持其饮水与体力。 | 系统 | **不做** | 宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。 |
| 35 | `jiasi` | 装死 | — | 在战斗中假装死亡以脱离战斗。 | 战斗 | **不做** | 假死/脱离战斗判定未实现，且属玩家对抗向玩法。 |
| 36 | `jiebai` | 结拜 | <某人> | 与另一名玩家结为异姓兄弟或姐妹。 | 社交 | **待实现** | 关系模型已有好感/信任，但没有结拜关系与仪式；可在关系系统上扩展。 |
| 37 | `kantou` | 割头 | <尸体> | 从尸体上割下首级。 | 物品 | **不做** | 尸体与死亡掉落未建模。 |
| 38 | `kill` | 杀、击杀 | <人物> | 对目标下杀手，进入生死战斗。 | 战斗 | **不做** | kill 动词被列入 src/wsim/api/session.py:216-217 的 FORBIDDEN_VERBS，并在 :2181 逐动词拦截；对 NPC 的战斗请用 fight，本版不开放玩家击杀。 |
| 39 | `list` | 看货、货单 | [<玩家ID>] | 查看当前店铺出售的货物、价格与库存。 | 经济 | **已实现** | 房间 services_detail 与店铺只读视图（src/wsim/api/session.py）已提供货物、价格、库存。 |
| 40 | `locate` | 找物 | <物品> | 在任务中定位指定任务物品所在的房间。 | 系统 | **待实现** | 任务系统只有委托接取/交付，没有任务物品坐标与定位索引。 |
| 41 | `location` | 寻人 | <玩家ID> | 查看某个在线玩家当前所在的位置。 | 系统 | **已实现** | src/wsim/api/session.py 的 _online_players_view 提供 location 字段；本版对所有玩家开放，不设门派技能门槛。 |
| 42 | `look` | 看、观察 | [<物品>\|<生物>\|<方向>] | 查看房间、人物、物品或某个方向的描述。 | 系统 | **已实现** | session._location_view（src/wsim/api/session.py）返回房间描述、出口、NPC 与店铺。 |
| 43 | `mbless` | 月老贺婚 | <男名> with <女名> | 以月老身份向新婚夫妇道贺。 | 社交 | **不做** | 婚礼仪式与广播未建模。 |
| 44 | `open` | 开门 | <门> 或 <方向> | 打开一扇门或某个方向的出入口。 | 物品 | **不做** | 门、容器与开关状态均未建模；本版房间不设可开关门。 |
| 45 | `piao` | 嫖 | <某人> | 在青楼消费寻欢。本版此类场所不开放。 | 社交 | **不做** | 青楼与相关消费场景未建模，且属产品明确排除内容。 |
| 46 | `pour` | 倒药 | <药物> in <容器> | 把药物倒入容器中调制。 | 物品 | **不做** | 容器与药物调制未建模。 |
| 47 | `put` | 放入 | <物品> in <容器> | 把物品放进容器。 | 物品 | **不做** | 容器、容量与放入/取出关系未建模；本版背包为扁平列表。 |
| 48 | `qiecuo` | 求教绝招 | <基本功>[.<绝招>] | 通过与指定对手切磋，验证并解锁某项绝招。 | 门派 | **待实现** | 依赖绝招/perform 系统；技能目前只有 0..1 熟练度，没有招式位。 |
| 49 | `qixing` | 布阵、北斗阵 | <敌手> <阵友1>…<阵友6> | 布下天罡北斗阵与敌手对抗。 | 门派 | **不做** | 多人阵法与全真门派专属玩法未建模，V1 不开放。 |
| 50 | `release` | 放生 | — | 放走自己的动物。 | 系统 | **不做** | 宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。 |
| 51 | `remove` | 脱下、卸甲 | all \| <物品> | 脱下身上穿戴的防具。 | 物品 | **已实现** | UnequipAction（src/wsim/actions/equipment.py）；`remove all` 可一次卸下全部防具。 |
| 52 | `reply` | 回话 | <讯息> | 回复最近一次私聊。 | 社交 | **不做** | 玩家私聊频道未建模。 |
| 53 | `ride` | 骑乘 | <生物> | 骑上马匹或其他坐骑。 | 系统 | **不做** | 坐骑与骑行移动未建模。 |
| 54 | `say` | 说话 | <讯息> | 在当前房间说话，同房间的人可见。 | 社交 | **不做** | 玩家间房间广播未建模；V1 只支持与 NPC 的 TalkAction。 |
| 55 | `semote` | 查动作 | [词首] | 按词首列出可用的感情动作。 | 社交 | **不做** | 玩家表情动作与广播未建模；本版不开放表情检索。 |
| 56 | `setwmsg` | 定兵描 | -w\|-u <描述> | 设置自制兵器在战斗中的描述文字。 | 物品 | **不做** | 自制兵器与战斗描述未建模。 |
| 57 | `shexin` | 慑心 | <某人> to <指令> | 以慑心术控制他人执行一条指令。 | 门派 | **不做** | 精神控制类门派技能未建模，且属 PK 向玩法。 |
| 58 | `shop` | 店务 | [open\|close\|reset\|owner …] | 巫师管理店铺开关、归属与重置。 | 经济 | **不做** | 这是巫师管理指令；玩家侧摆摊用 baitan/open_shop，不开放原始店务后台。 |
| 59 | `shoplist` | 查价、物价 | <物品id或中文名> | 查询某物品在各店铺的售价与库存。 | 经济 | **已实现** | 房间 services_detail 与店铺只读视图（src/wsim/api/session.py）提供价格/库存。 |
| 60 | `sleep` | 睡觉、歇息 | [<人物>] | 在客栈或安全处睡觉，恢复体力。 | 系统 | **待实现** | PlayerRestAction 定义在 src/wsim/actions/player.py:433，但 src/wsim/actions/registry.py:98-126 的动作注册列表未包含它，rest 尚不可执行。 |
| 61 | `stay` | 留宠 | <动物> | 让跟随的动物留在原地。 | 系统 | **不做** | 宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。 |
| 62 | `steal` | 偷窃 | <物品> from <某人> | 从他人身上偷取物品。 | 社交 | **不做** | PK/偷抢属产品明确排除内容。 |
| 63 | `stop` | 止宠 | <动物> | 让动物停止攻击。 | 系统 | **不做** | 宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。 |
| 64 | `surrender` | 投降 | — | 在战斗中认输，结束战斗。 | 战斗 | **待实现** | FightAction（src/wsim/actions/combat.py）已有胜负结算；主动认输/脱离战斗尚未实现。 |
| 65 | `task` | 任务榜、委托榜 | [-u] | 查看当前可接的委托榜。 | 系统 | **已实现** | session._quest_board_view 与 GET /api/player/quests；QuestAcceptAction/QuestDeliverAction（src/wsim/actions/quests.py）。 |
| 66 | `team` | 组队 | with <某人> \| … | 与其他玩家组成队伍共同行动。 | 社交 | **不做** | 组队与队伍战斗未建模；V1 为单人江湖。 |
| 67 | `tell` | 传话、私聊 | <某人> <讯息> | 向指定玩家发送私聊讯息。 | 社交 | **不做** | 玩家间私聊频道未建模。 |
| 68 | `touxi` | 偷袭 | <人物> | 在战斗开始前偷袭目标，取得先手。 | 战斗 | **待实现** | FightAction（src/wsim/actions/combat.py）已有回合战斗；偷袭需要先手/潜行判定，尚未实现。 |
| 69 | `train` | 驯兽 | <动物> | 训练动物，使其听从指令。 | 系统 | **不做** | 宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。 |
| 70 | `trap` | 设陷阱 | [<方向>] | 在某个方向布下陷阱。 | 战斗 | **不做** | 陷阱与移动触发判定未建模。 |
| 71 | `tune` | 频道 | [<频道>\|on\|off] | 开关或切换聊天频道。 | 社交 | **不做** | 玩家聊天频道与订阅关系未建模；本版不开放频道切换。 |
| 72 | `unride` | 下骑 | — | 从坐骑上下来，结束骑乘状态。 | 系统 | **不做** | 坐骑实体与骑乘移动未建模；本版只支持步行。 |
| 73 | `unwield` | 收兵器、卸武 | <物品> | 收起手中挥动的兵器。 | 物品 | **已实现** | UnequipAction（src/wsim/actions/equipment.py）；与 remove 共用装备槽。 |
| 74 | `vote/chblk` | 封禁表决 | <某人> | 表决封禁某人的聊天频道。 | 系统 | **不做** | vote 子命令；治理系统未建模。 |
| 75 | `vote/jail` | 监禁表决 | <某人> | 表决将某人投入监牢。 | 系统 | **不做** | vote 子命令；治理系统未建模。 |
| 76 | `vote/unchblk` | 解封表决 | <某人> | 表决解除某人的频道封禁。 | 系统 | **不做** | vote 子命令；治理系统未建模。 |
| 77 | `vote/unjail` | 释放表决 | <某人> | 表决释放被监禁的某人。 | 系统 | **不做** | vote 子命令；治理系统未建模。 |
| 78 | `vote` | 投票 | <动议> <某人> | 参与玩家治理动议的表决。 | 系统 | **不做** | 玩家治理/监禁系统未建模，V1 不开放。 |
| 79 | `wear` | 穿上、穿戴 | <装备> | 穿上防具或饰物。 | 物品 | **已实现** | EquipAction（src/wsim/actions/equipment.py）；按装备槽校验。 |
| 80 | `whisper` | 耳语 | <某人> <讯息> | 对同房间某人耳语。 | 社交 | **不做** | 玩家间私密频道未建模。 |
| 81 | `wield` | 持兵器、持械 | <兵器> | 把兵器持在手中。 | 物品 | **已实现** | EquipAction（src/wsim/actions/equipment.py）；equip 的武器槽。 |
| 82 | `alias` | 别名、快捷 | <别名> <原命令> | 为常用命令设置文字别名。 | 系统 | **待实现** | 文字壳尚未提供别名表；可在前端命令解析层实现，不涉及引擎。 |
| 83 | `boss` | 老板键 | <参数> | 快速遮盖游戏窗口以应对老板。 | 系统 | **不做** | 桌面端老板键功能，与本版 Web 文字壳无关。 |
| 84 | `bossgone` | 恢复游戏 | — | 恢复被老板键遮盖的游戏窗口。 | 系统 | **不做** | 桌面端功能，与 Web 文字壳无关。 |
| 85 | `cls` | 清屏 | [-l] | 清空当前屏幕上的输出。 | 系统 | **待实现** | 前端日志区没有清屏入口；需在文字壳补一个清空动作。 |
| 86 | `describe` | 自述、设定描述 | <描述\|none> | 设置或清除自己的角色描述。 | 社交 | **待实现** | 玩家档案只有角色/门派/技能，没有自定义描述字段。 |
| 87 | `exit` | 退出 | — | 退出游戏并离开当前世界。 | 系统 | **已实现** | PlayerLeaveAction（src/wsim/actions/player.py）；会话断开即离开。 |
| 88 | `finger` | 查人 | [<玩家>] | 查询某位玩家的门派、技能与在线状态。 | 社交 | **待实现** | 当前只有在线玩家列表（_online_players_view），没有玩家档案目录与离线查询。 |
| 89 | `help` | 帮助、说明 | [<主题>] | 打开帮助主题或命令说明。 | 系统 | **待实现** | content/xkx_help 内容包已产出并通过校验；游戏内帮助入口尚未接入该内容包。 |
| 90 | `hp` | 气血 | — | 查看自己的气血与当前战斗状态。 | 战斗 | **已实现** | src/wsim/api/session.py 的 _status_view 已展示 hp/max_hp；暂无独立的 hp 动词。 |
| 91 | `id` | 鉴定、物品详情 | [here] | 查看物品的类别、描述与参考价格。 | 物品 | **已实现** | src/wsim/api/session.py 的背包/店铺只读视图已给出物品 id、类别、描述与价格；`id here` 因无地面物品而退化为查看背包。 |
| 92 | `inventory` | 背包、物品 | — | 查看自己携带的物品与钱财。 | 物品 | **已实现** | session._wallet_view（src/wsim/api/session.py）返回钱包、背包与装备。 |
| 93 | `map` | 地图 | [<参量>] | 查看当前区域的地图与相邻节点。 | 移动 | **已实现** | GET /api/world/map（session.world_map，src/wsim/api/session.py）返回全城节点与邻接边。 |
| 94 | `mudlist` | 泥巴列表 | [<泥巴名>] | 列出互连的其他 MUD 站点。 | 系统 | **不做** | 本版为单世界复刻，不做 MUD 互连目录。 |
| 95 | `news` | 新闻 | [next\|<编号>\|new\|all] | 阅读游戏内新闻公告。 | 系统 | **不做** | 游戏内新闻板未建模；公告走运营渠道。 |
| 96 | `nick` | 外号 | <外号\|none> | 设置或清除自己的外号。 | 社交 | **待实现** | 玩家档案没有外号字段；显示名只有角色名。 |
| 97 | `passwd` | 改密 | — | 修改自己的登录密码。 | 系统 | **待实现** | 认证只支持注册/登录，没有改密接口。 |
| 98 | `quest` | 任务 | — | 查看自己已接的委托、进度与奖励。 | 系统 | **已实现** | GET /api/player/quests 与 session._quests_view（src/wsim/api/session.py）。 |
| 99 | `quit` | 离开 | — | 退出游戏并保存进度。 | 系统 | **已实现** | PlayerLeaveAction（src/wsim/actions/player.py）；进度由事件流自动持久化。 |
| 100 | `save` | 存档 | — | 手动保存当前进度。 | 系统 | **已实现** | src/wsim/world/writer.py 与 src/wsim/world/store.py 采用事件溯源，每个动作即写入事件流，无需手动存档。 |
| 101 | `score` | 状态、属性 | — | 查看体力、技能、装备、门派与财务概况。 | 系统 | **已实现** | src/wsim/api/session.py 的 _status_view 已展示体力、气血、攻防、技能、装备与门派；经验与完整属性仍待扩展。 |
| 102 | `set` | 设置 | <变量> <值> | 设置环境变量或显示选项。 | 系统 | **待实现** | 本版没有玩家设置项；配置由服务端默认值决定。 |
| 103 | `sos` | 求救 | [post\|read] | 向巫师或在线玩家发出求救。 | 系统 | **不做** | 在线管理员求助通道未建模。 |
| 104 | `suicide` | 自尽 | [-f\|-r] | 删除自己的角色并重新开始。 | 系统 | **不做** | 破坏性角色删除不在 V1 开放；账号/角色管理走运营流程。 |
| 105 | `time` | 时间 | — | 查看当前游戏世界时间。 | 系统 | **已实现** | src/wsim/api/session.py 的玩家状态返回世界 tick，界面按第几天几时显示；暂无农历/节气。 |
| 106 | `title` | 称号 | [玩家名] | 查看或设置称号。 | 社交 | **待实现** | 玩家档案没有称号字段；称号需与成就/门派贡献一起设计。 |
| 107 | `unset` | 取消设置 | <变量> | 取消某个环境变量设置。 | 系统 | **待实现** | 本版没有玩家设置项。 |
| 108 | `version` | 版本 | — | 查看服务端版本与内容包版本。 | 系统 | **已实现** | GET /api/health 的 version/profile_version 字段（src/wsim/api/server.py）。 |
| 109 | `who` | 在线、谁在 | — | 列出当前在线玩家及其位置。 | 社交 | **已实现** | session._online_players_view（src/wsim/api/session.py）；玩家状态里直接可见。 |
| 110 | `wimpy` | 逃命线 | [<百分比>] | 设置自动逃命的体力阈值。 | 战斗 | **待实现** | 战斗已有回合与体力；自动逃跑阈值与 flee 动作尚未实现。 |
| 111 | `wizlist` | 巫师名单 | — | 列出当前在线的巫师。 | 系统 | **不做** | 本版没有游戏内巫师在线体系。 |
| 112 | `abandon` | 弃功、放弃 | <技能> | 彻底放弃某项已学技能。 | 门派 | **待实现** | 技能以熟练度存在于玩家状态，但没有删除/放弃动作。 |
| 113 | `apprentice` | 拜师 | [cancel]\|<对象> | 拜某位 NPC 或玩家为师，加入其门派。 | 门派 | **待实现** | ApprenticeAction（src/wsim/actions/social.py）已存在，但玩家动词白名单 src/wsim/actions/player.py 的 PLAYER_UNIMPLEMENTED_VERBS 显式未开放。 |
| 114 | `bai` | 拜师（同 apprentice） | [cancel]\|<对象> | 拜师，加入师父所在门派。 | 门派 | **待实现** | apprentice 的别名；同样受 PLAYER_UNIMPLEMENTED_VERBS 限制。 |
| 115 | `bei` | 备招、备兵（同 prepare） | [<拳术>\|<拳术>\|none] | 准备下一回合要使用的招式或兵器。 | 门派 | **待实现** | 本版技能只有熟练度，没有招式位/准备槽；随武学系统扩展。 |
| 116 | `burning` | 发怒、燃烧 | — | 激发怒气，短时间内提升攻击。 | 战斗 | **待实现** | 战斗已有攻防结算；怒气资源与增益效果尚未建模。 |
| 117 | `cha` | 技能（同 skills） | [<某人>] [<技能>] | 查看自己或他人的技能清单与熟练度。 | 系统 | **已实现** | session._skills_detail（src/wsim/api/session.py）；skills 的别名。 |
| 118 | `dazuo` | 打坐（同 exercise） | <耗费气的量> | 打坐练气，把精转化为气。 | 门派 | **待实现** | 本版没有精/气/内力资源；exercise 的别名。 |
| 119 | `du` | 读书（同 study） | <物品> <次数> | 阅读书籍，提升对应知识技能。 | 门派 | **待实现** | 书籍与阅读系统未建模；study 的别名。 |
| 120 | `editskill` | 编技能 | <基本技能> <英文名> <中文名> <第几招> | 巫师创建或修改自创武功。 | 系统 | **不做** | 巫师造技能工具；本版不做玩家自创武功。 |
| 121 | `enable` | 激发 | [<技能种类> <技能名称>\|none] | 把某项武学激发到对应技能种类上。 | 门派 | **待实现** | 技能没有种类/激发槽，只有名称与熟练度。 |
| 122 | `enforce` | 加力 | <内力点数>\|none | 把内力加到攻击上，提高伤害并消耗内力。 | 战斗 | **待实现** | 战斗已有攻防结算；内力资源与加力加成尚未建模。 |
| 123 | `exercise` | 练气、打坐 | <耗费气的量> | 打坐练气，把精转化为气。 | 门派 | **待实现** | 本版没有精/气/内力资源，休息只恢复体力。 |
| 124 | `exert` | 运功 | <功能名称> [<对象>] | 运功施展疗伤、护体等特殊功能。 | 战斗 | **待实现** | 战斗已有攻防结算；运功疗伤/护体等状态尚未建模。 |
| 125 | `expell` | 逐徒、开除 | <某人> | 把弟子逐出自己创建的门派。 | 门派 | **已实现** | PlayerExpelDiscipleAction（src/wsim/actions/player_sect.py）；仅限玩家自创门派，NPC 师父逐徒未开放。 |
| 126 | `fangqi` | 放弃（同 abandon） | <技能> | 放弃某项已学技能，清空其熟练度。 | 门派 | **待实现** | abandon 的别名；技能删除动作尚未实现。 |
| 127 | `fennu` | 发怒（同 burning） | — | 激发怒气提升攻击。 | 战斗 | **待实现** | burning 的别名；怒气系统未建模。 |
| 128 | `jiali` | 加力（同 enforce） | <内力点数>\|none | 把内力加到攻击上。 | 战斗 | **待实现** | enforce 的别名；内力系统未建模。 |
| 129 | `jiao` | 教习（同 teach） | <某人> <技能> | 教授弟子某项技能。 | 门派 | **待实现** | 玩家可收徒但无授课动作；teach 的别名。 |
| 130 | `jifa` | 激发（同 enable） | [<技能种类> <技能名称>\|none] | 把武学激发到技能种类上。 | 门派 | **待实现** | enable 的别名；技能无激发槽。 |
| 131 | `jingzuo` | 静坐 | <精气点数> | 静坐修炼，消耗精气提升内力。 | 门派 | **待实现** | 精/气/内力资源未建模。 |
| 132 | `kaichu` | 逐徒（同 expell） | <某人> | 把弟子逐出师门。 | 门派 | **已实现** | expell 的别名；PlayerExpelDiscipleAction（src/wsim/actions/player_sect.py）。 |
| 133 | `learn` | 学习、请教 | <某人> <技能> <次数> | 向师父或他人学习技能。 | 门派 | **待实现** | 拜师被 PLAYER_UNIMPLEMENTED_VERBS 拦住，师父授课动作也未建模。 |
| 134 | `lian` | 练功、练习（同 practice） | <技能种类> <次数> | 反复练习某项技能，提升熟练度。 | 门派 | **已实现** | practice 的别名；CultivateAction（src/wsim/actions/basic.py）按小时推进技能熟练度。 |
| 135 | `mylian` | 自练 | <技能种类> | 自练自创武功，提升自创招式。 | 门派 | **待实现** | 自创武功系统未建模。 |
| 136 | `perform` | 绝招、发招 | [<武功种类>.]<招式> [<对象>] | 在战斗中施展绝招。 | 战斗 | **待实现** | 战斗已有回合结算；招式位与绝招尚未建模。 |
| 137 | `persuade` | 劝架 | <某人> stop | 劝正在战斗的双方停手。 | 社交 | **不做** | 战斗与劝架判定未建模；V1 不开放 PK 向互动。 |
| 138 | `practice` | 练习、练功 | <技能种类> <次数> | 反复练习技能，提升熟练度。 | 门派 | **已实现** | CultivateAction（src/wsim/actions/basic.py）；技能熟练度 0..1，按投入小时推进。 |
| 139 | `prepare` | 准备、备招 | [<拳术>\|<拳术>\|none] | 准备下一回合使用的招式或兵器。 | 门派 | **待实现** | 技能没有招式位/准备槽。 |
| 140 | `quanjia` | 劝架（同 persuade） | <某人> stop | 劝正在战斗的双方停手。 | 社交 | **不做** | persuade 的别名；战斗只对 NPC 开放，劝架/停战属玩家对抗，未开放。 |
| 141 | `recruit` | 收徒 | [cancel]\|<对象> | 收某人为自己的弟子。 | 门派 | **已实现** | PlayerRecruitDiscipleAction（src/wsim/actions/player_sect.py）与 RecruitAction（src/wsim/actions/social.py）；仅限玩家自创门派。 |
| 142 | `research` | 研究 | <技能> <次数> | 研究武学，提升理论或自创武功。 | 门派 | **待实现** | 研究/自创武功系统未建模。 |
| 143 | `respirate` | 吐纳 | [<耗费精的量>] | 吐纳练气，把精转化为气。 | 门派 | **待实现** | 本版没有精、气、内力三种资源，只有体力；吐纳链路无法结算。 |
| 144 | `shou` | 收徒（同 recruit） | [cancel]\|<对象> | 收某人为自己的弟子，纳入门下。 | 门派 | **已实现** | recruit 的别名；PlayerRecruitDiscipleAction（src/wsim/actions/player_sect.py）。 |
| 145 | `skills` | 技能 | [<某人>] [<技能>] | 查看自己或他人的技能清单与熟练度。 | 系统 | **已实现** | session._skills_detail（src/wsim/api/session.py）返回全部技能与熟练度。 |
| 146 | `study` | 读书 | <物品> <次数> | 阅读书籍提升知识技能。 | 门派 | **待实现** | 书籍与阅读系统未建模。 |
| 147 | `teach` | 教习 | <某人> <技能> | 向自己的弟子传授指定技能。 | 门派 | **待实现** | 玩家可收徒但没有授课动作。 |
| 148 | `tuna` | 吐纳（同 respirate） | [<耗费精的量>] | 吐纳练气，把精转化为气。 | 门派 | **待实现** | respirate 的别名；精/气资源未建模。 |
| 149 | `xue` | 学习（同 learn） | <某人> <技能> <次数> | 向他人学习技能。 | 门派 | **待实现** | learn 的别名；拜师/授课链路未开放。 |
| 150 | `yanjiu` | 研究（同 research） | <技能> <次数> | 研究武学，积累理论或自创进度。 | 门派 | **待实现** | research 的别名；研究系统未建模。 |
| 151 | `yanlian` | 融功 | <技能> | 把两门相近武学融会贯通。 | 门派 | **不做** | 技能合并会改变技能树与平衡，本版技能为扁平熟练度，明确不做。 |
| 152 | `yong` | 绝招（同 perform） | [<武功种类>.]<招式> [<对象>] | 在战斗中施展指定绝招。 | 战斗 | **待实现** | perform 的别名；招式系统未建模。 |
| 153 | `yun` | 运功（同 exert） | <功能名称> [<对象>] | 运功施展特殊功能。 | 战斗 | **待实现** | exert 的别名；内力功能未建模。 |

## 四、引擎现在根本做不到的命令（取舍清单）

### 4.1 决定不做（62 条）

**PK/偷抢/对抗（7 条）**

- `guard`（护卫）：护卫/拦截玩法依赖战斗与实时对抗，本版不开放。
- `jiasi`（装死）：假死/脱离战斗判定未实现，且属玩家对抗向玩法。
- `kill`（杀、击杀）：kill 动词被列入 src/wsim/api/session.py:216-217 的 FORBIDDEN_VERBS，并在 :2181 逐动词拦截；对 NPC 的战斗请用 fight，本版不开放玩家击杀。
- `steal`（偷窃）：PK/偷抢属产品明确排除内容。
- `trap`（设陷阱）：陷阱与移动触发判定未建模。
- `persuade`（劝架）：战斗与劝架判定未建模；V1 不开放 PK 向互动。
- `quanjia`（劝架）：persuade 的别名；战斗只对 NPC 开放，劝架/停战属玩家对抗，未开放。

**产品排除/无对应模型（8 条）**

- `beg`（乞讨）：乞讨会绕过交易与劳动产出，产品上不开放。
- `follow`（跟随）：玩家/NPC 跟随关系未建模，V1 只支持固定房间移动。
- `gamble`（赌博）：赌博属产品明确排除内容，且会破坏经济模型。
- `halt`（停手）：本版动作按事件原子结算，没有可中断的忙碌状态。
- `piao`（嫖）：青楼与相关消费场景未建模，且属产品明确排除内容。
- `setwmsg`（定兵描）：自制兵器与战斗描述未建模。
- `editskill`（编技能）：巫师造技能工具；本版不做玩家自创武功。
- `yanlian`（融功）：技能合并会改变技能树与平衡，本版技能为扁平熟练度，明确不做。

**地面物品/容器（6 条）**

- `close`（关门）：门与容器未建模；本版房间不设可开关门。
- `drop`（丢下、扔掉）：地面物品与无主物品模型未建模；V1 明确不做地面掉落。
- `kantou`（割头）：尸体与死亡掉落未建模。
- `open`（开门）：门、容器与开关状态均未建模；本版房间不设可开关门。
- `pour`（倒药）：容器与药物调制未建模。
- `put`（放入）：容器、容量与放入/取出关系未建模；本版背包为扁平列表。

**宠物/坐骑（10 条）**

- `attack`（唆使）：宠物与坐骑实体、驯服度均未建模；本版不开放驯兽与宠物参战。
- `come`（唤宠）：宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。
- `feed`（喂养）：宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。
- `imbibe`（喂饮）：宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。
- `release`（放生）：宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。
- `ride`（骑乘）：坐骑与骑行移动未建模。
- `stay`（留宠）：宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。
- `stop`（止宠）：宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。
- `train`（驯兽）：宠物与坐骑实体、驯服度均未建模；本版不开放宠物玩法。
- `unride`（下骑）：坐骑实体与骑乘移动未建模；本版只支持步行。

**玩家治理/巫师/运营（14 条）**

- `bugs`（报错）：本版用仓库 Issue 跟踪缺陷，不提供游戏内报错入口。
- `shop`（店务）：这是巫师管理指令；玩家侧摆摊用 baitan/open_shop，不开放原始店务后台。
- `vote/chblk`（封禁表决）：vote 子命令；治理系统未建模。
- `vote/jail`（监禁表决）：vote 子命令；治理系统未建模。
- `vote/unchblk`（解封表决）：vote 子命令；治理系统未建模。
- `vote/unjail`（释放表决）：vote 子命令；治理系统未建模。
- `vote`（投票）：玩家治理/监禁系统未建模，V1 不开放。
- `boss`（老板键）：桌面端老板键功能，与本版 Web 文字壳无关。
- `bossgone`（恢复游戏）：桌面端功能，与 Web 文字壳无关。
- `mudlist`（泥巴列表）：本版为单世界复刻，不做 MUD 互连目录。
- `news`（新闻）：游戏内新闻板未建模；公告走运营渠道。
- `sos`（求救）：在线管理员求助通道未建模。
- `suicide`（自尽）：破坏性角色删除不在 V1 开放；账号/角色管理走运营流程。
- `wizlist`（巫师名单）：本版没有游戏内巫师在线体系。

**玩家频道/社交广播（14 条）**

- `ce`（私语）：玩家间私聊频道未建模；婚姻系统只记录配偶关系。
- `demote`（查表情）：表情系统未建模；文字壳不提供 emote 变体查询。
- `emote`（表情）：玩家间表情广播未建模；本版只保留 NPC 交谈。
- `femote`（找表情）：玩家表情动作与广播未建模；本版不开放表情检索。
- `han`（喊话、大喊）：区域广播/频道未建模。
- `hbless`（贺婚）：婚礼仪式与广播未建模；婚姻只用 MarriageAction 记录关系。
- `mbless`（月老贺婚）：婚礼仪式与广播未建模。
- `reply`（回话）：玩家私聊频道未建模。
- `say`（说话）：玩家间房间广播未建模；V1 只支持与 NPC 的 TalkAction。
- `semote`（查动作）：玩家表情动作与广播未建模；本版不开放表情检索。
- `team`（组队）：组队与队伍战斗未建模；V1 为单人江湖。
- `tell`（传话、私聊）：玩家间私聊频道未建模。
- `tune`（频道）：玩家聊天频道与订阅关系未建模；本版不开放频道切换。
- `whisper`（耳语）：玩家间私密频道未建模。

**门派专属（3 条）**

- `cuifa`（催发）：门派控制类毒药与生死控制未建模，且属于 PK 向玩法，本版不开放。
- `qixing`（布阵、北斗阵）：多人阵法与全真门派专属玩法未建模，V1 不开放。
- `shexin`（慑心）：精神控制类门派技能未建模，且属 PK 向玩法。

### 4.2 现在做不到、但可立项（51 条）

**任务系统（1 条）**

- `locate`（找物）：任务系统只有委托接取/交付，没有任务物品坐标与定位索引。

**师承系统（6 条）**

- `apprentice`（拜师）：ApprenticeAction（src/wsim/actions/social.py）已存在，但玩家动词白名单 src/wsim/actions/player.py 的 PLAYER_UNIMPLEMENTED_VERBS 显式未开放。
- `bai`（拜师）：apprentice 的别名；同样受 PLAYER_UNIMPLEMENTED_VERBS 限制。
- `jiao`（教习）：玩家可收徒但无授课动作；teach 的别名。
- `learn`（学习、请教）：拜师被 PLAYER_UNIMPLEMENTED_VERBS 拦住，师父授课动作也未建模。
- `teach`（教习）：玩家可收徒但没有授课动作。
- `xue`（学习）：learn 的别名；拜师/授课链路未开放。

**战利品系统（1 条）**

- `get`（拿、捡）：战斗掉落只产出 loot_create 效果（src/wsim/world/effects.py:104 定义、:422 处理），src/wsim/actions/combat.py:124 在战斗结算里发出该效果，但没有 LootAction 消费它；地面物品模型也未建模。

**战斗系统（13 条）**

- `ansuan`（暗算）：基础回合战斗已由 FightAction（src/wsim/actions/combat.py）提供；暗算还缺潜行/先手判定。
- `hit`（出手、进招）：FightAction（src/wsim/actions/combat.py）已有完整回合战斗；hit 需要单次攻击接口，文字壳尚未挂动词。
- `surrender`（投降）：FightAction（src/wsim/actions/combat.py）已有胜负结算；主动认输/脱离战斗尚未实现。
- `touxi`（偷袭）：FightAction（src/wsim/actions/combat.py）已有回合战斗；偷袭需要先手/潜行判定，尚未实现。
- `wimpy`（逃命线）：战斗已有回合与体力；自动逃跑阈值与 flee 动作尚未实现。
- `burning`（发怒、燃烧）：战斗已有攻防结算；怒气资源与增益效果尚未建模。
- `enforce`（加力）：战斗已有攻防结算；内力资源与加力加成尚未建模。
- `exert`（运功）：战斗已有攻防结算；运功疗伤/护体等状态尚未建模。
- `fennu`（发怒）：burning 的别名；怒气系统未建模。
- `jiali`（加力）：enforce 的别名；内力系统未建模。
- `perform`（绝招、发招）：战斗已有回合结算；招式位与绝招尚未建模。
- `yong`（绝招）：perform 的别名；招式系统未建模。
- `yun`（运功）：exert 的别名；内力功能未建模。

**武学/资源系统（17 条）**

- `qiecuo`（求教绝招）：依赖绝招/perform 系统；技能目前只有 0..1 熟练度，没有招式位。
- `abandon`（弃功、放弃）：技能以熟练度存在于玩家状态，但没有删除/放弃动作。
- `bei`（备招、备兵）：本版技能只有熟练度，没有招式位/准备槽；随武学系统扩展。
- `dazuo`（打坐）：本版没有精/气/内力资源；exercise 的别名。
- `du`（读书）：书籍与阅读系统未建模；study 的别名。
- `enable`（激发）：技能没有种类/激发槽，只有名称与熟练度。
- `exercise`（练气、打坐）：本版没有精/气/内力资源，休息只恢复体力。
- `fangqi`（放弃）：abandon 的别名；技能删除动作尚未实现。
- `jifa`（激发）：enable 的别名；技能无激发槽。
- `jingzuo`（静坐）：精/气/内力资源未建模。
- `mylian`（自练）：自创武功系统未建模。
- `prepare`（准备、备招）：技能没有招式位/准备槽。
- `research`（研究）：研究/自创武功系统未建模。
- `respirate`（吐纳）：本版没有精、气、内力三种资源，只有体力；吐纳链路无法结算。
- `study`（读书）：书籍与阅读系统未建模。
- `tuna`（吐纳）：respirate 的别名；精/气资源未建模。
- `yanjiu`（研究）：research 的别名；研究系统未建模。

**玩家档案/文字壳（10 条）**

- `alias`（别名、快捷）：文字壳尚未提供别名表；可在前端命令解析层实现，不涉及引擎。
- `cls`（清屏）：前端日志区没有清屏入口；需在文字壳补一个清空动作。
- `describe`（自述、设定描述）：玩家档案只有角色/门派/技能，没有自定义描述字段。
- `finger`（查人）：当前只有在线玩家列表（_online_players_view），没有玩家档案目录与离线查询。
- `help`（帮助、说明）：content/xkx_help 内容包已产出并通过校验；游戏内帮助入口尚未接入该内容包。
- `nick`（外号）：玩家档案没有外号字段；显示名只有角色名。
- `passwd`（改密）：认证只支持注册/登录，没有改密接口。
- `set`（设置）：本版没有玩家设置项；配置由服务端默认值决定。
- `title`（称号）：玩家档案没有称号字段；称号需与成就/门派贡献一起设计。
- `unset`（取消设置）：本版没有玩家设置项。

**社交系统（2 条）**

- `give`（给、赠送）：现有 TradeAction 只支持双方对价交易，没有单向转移动作；可复用装备/背包模型后补。
- `jiebai`（结拜）：关系模型已有好感/信任，但没有结拜关系与仪式；可在关系系统上扩展。

**系统动作未注册（1 条）**

- `sleep`（睡觉、歇息）：PlayerRestAction 定义在 src/wsim/actions/player.py:433，但 src/wsim/actions/registry.py:98-126 的动作注册列表未包含它，rest 尚不可执行。

### 4.3 排期建议

1. **P0 战斗系统**：补 kill/hit/偷袭等动词与战斗内资源，覆盖 13 条命令。
2. **P1 武学与资源**：补技能种类、激发/准备槽、精/气/内力与招式位，覆盖 17 条武学命令。
3. **P2 师承、社交、任务与战利品**：开放拜师/授课，补打听话题、赠送、结拜、任务定位与拾取，覆盖 10 条。
4. **P3 玩家档案与文字壳**：补别名、清屏、称号、描述、改密、设置与休息注册，覆盖 11 条。
5. **不排期**：4.1 的 62 条按产品取舍关闭；若要重启，必须单独立项并先改架构。

## 五、已实现命令的引擎落点

共 40 条。逐条落点见上表「依据」列；均为 `src/`、`GET /api/` 或 `content/` 中可核对的动作/视图。

- `ask`：TalkAction（src/wsim/actions/social.py）已注册，文字壳已接「问/打听」，可自由交谈；尚无 xkx 话题表。
- `baitan`：OpenShopAction（src/wsim/actions/business.py）支持摊位型店铺；RestockAction/SetPriceAction 管库存与定价。
- `buy`：BuyAction（src/wsim/actions/commerce.py）；价格与库存由店铺状态校验。
- `check`：session._present_view 与 _skills_detail（src/wsim/api/session.py）给出在场人物技能明细；暂不支持查离线玩家。
- `chi`：eat 的别名；ConsumeAction（src/wsim/actions/basic.py）结算消耗与体力恢复。
- `dating`：check 的别名；复用 _present_view/_skills_detail（src/wsim/api/session.py）。
- `drink`：he 的等价命令；ConsumeAction（src/wsim/actions/basic.py）与 UseItemAction（src/wsim/actions/equipment.py）。
- `eat`：ConsumeAction（src/wsim/actions/basic.py）；食物耗尽后从背包移除。
- `fight`：FightAction（src/wsim/actions/combat.py）已注册，文字壳的 fight/打/战斗/出手 可直接调用；单场最多 20 回合。
- `go`：MoveAction（src/wsim/actions/basic.py）；出口校验见 session._location_view。
- `he`：drink 的别名；ConsumeAction（src/wsim/actions/basic.py）。
- `list`：房间 services_detail 与店铺只读视图（src/wsim/api/session.py）已提供货物、价格、库存。
- `location`：src/wsim/api/session.py 的 _online_players_view 提供 location 字段；本版对所有玩家开放，不设门派技能门槛。
- `look`：session._location_view（src/wsim/api/session.py）返回房间描述、出口、NPC 与店铺。
- `remove`：UnequipAction（src/wsim/actions/equipment.py）；`remove all` 可一次卸下全部防具。
- `shoplist`：房间 services_detail 与店铺只读视图（src/wsim/api/session.py）提供价格/库存。
- `task`：session._quest_board_view 与 GET /api/player/quests；QuestAcceptAction/QuestDeliverAction（src/wsim/actions/quests.py）。
- `unwield`：UnequipAction（src/wsim/actions/equipment.py）；与 remove 共用装备槽。
- `wear`：EquipAction（src/wsim/actions/equipment.py）；按装备槽校验。
- `wield`：EquipAction（src/wsim/actions/equipment.py）；equip 的武器槽。
- `exit`：PlayerLeaveAction（src/wsim/actions/player.py）；会话断开即离开。
- `hp`：src/wsim/api/session.py 的 _status_view 已展示 hp/max_hp；暂无独立的 hp 动词。
- `id`：src/wsim/api/session.py 的背包/店铺只读视图已给出物品 id、类别、描述与价格；`id here` 因无地面物品而退化为查看背包。
- `inventory`：session._wallet_view（src/wsim/api/session.py）返回钱包、背包与装备。
- `map`：GET /api/world/map（session.world_map，src/wsim/api/session.py）返回全城节点与邻接边。
- `quest`：GET /api/player/quests 与 session._quests_view（src/wsim/api/session.py）。
- `quit`：PlayerLeaveAction（src/wsim/actions/player.py）；进度由事件流自动持久化。
- `save`：src/wsim/world/writer.py 与 src/wsim/world/store.py 采用事件溯源，每个动作即写入事件流，无需手动存档。
- `score`：src/wsim/api/session.py 的 _status_view 已展示体力、气血、攻防、技能、装备与门派；经验与完整属性仍待扩展。
- `time`：src/wsim/api/session.py 的玩家状态返回世界 tick，界面按第几天几时显示；暂无农历/节气。
- `version`：GET /api/health 的 version/profile_version 字段（src/wsim/api/server.py）。
- `who`：session._online_players_view（src/wsim/api/session.py）；玩家状态里直接可见。
- `cha`：session._skills_detail（src/wsim/api/session.py）；skills 的别名。
- `expell`：PlayerExpelDiscipleAction（src/wsim/actions/player_sect.py）；仅限玩家自创门派，NPC 师父逐徒未开放。
- `kaichu`：expell 的别名；PlayerExpelDiscipleAction（src/wsim/actions/player_sect.py）。
- `lian`：practice 的别名；CultivateAction（src/wsim/actions/basic.py）按小时推进技能熟练度。
- `practice`：CultivateAction（src/wsim/actions/basic.py）；技能熟练度 0..1，按投入小时推进。
- `recruit`：PlayerRecruitDiscipleAction（src/wsim/actions/player_sect.py）与 RecruitAction（src/wsim/actions/social.py）；仅限玩家自创门派。
- `shou`：recruit 的别名；PlayerRecruitDiscipleAction（src/wsim/actions/player_sect.py）。
- `skills`：session._skills_detail（src/wsim/api/session.py）返回全部技能与熟练度。

## 六、复跑与校验

LPC 源码不入库，clone 到仓库外（例如 `/tmp/xkx-research/fluffos-xkx100`）后执行：

```bash
python3 tools/xkx_help/build.py --src /tmp/xkx-research/fluffos-xkx100
python3 tools/xkx_help/build.py --verify
python3 -m pytest tests/test_xkx_help.py -q
```

`inventory.txt` 与 `topics_index.txt` 是冻结口径；源码条数或主题索引漂移时，`--src` 会直接报错，不会静默改写矩阵。
输出不含时间戳，重复运行应逐字节一致。

## 七、帮助接入字段契约（玩家壳照此接线）

本内容包只读消费，玩家壳不得因接线修改 `ui/**` 或 `src/wsim/api/**`；壳侧只读取 `content/xkx_help/` 并按下列字段渲染。

### 7.1 主题对象

来源 `content/xkx_help/topics.yaml`。文件结构：`schema_version`、`source`、`topics[]`。

| 字段 | 类型 | 约束 | 接头用途 |
| --- | --- | --- | --- |
| `id` | string | 必填；稳定标识，与 `tools/xkx_help/topics_index.txt` 一致 | 路由键；`help <id>` 直接命中 |
| `title` | string | 必填；界面标题 | 列表与详情页标题 |
| `category` | string | 必填；枚举：入门、规则、设置、管理、角色、系统、社交、移动、门派、战斗、任务、经济、生活 | 侧栏/分页分组 |
| `status` | string | 必填；枚举：开放、部分开放、未开放、管理 | 未开放/管理主题置灰或加提示 |
| `body` | string | 必填；多行中文，YAML `\|` 块文本，保留换行 | 正文渲染；按行切段，不假设 Markdown |

### 7.2 命令对象

来源 `content/xkx_help/commands.yaml`。文件结构：`schema_version`、`source`、`commands[]`；顺序已按源码层（std/usr/skill）冻结。

| 字段 | 类型 | 约束 | 接头用途 |
| --- | --- | --- | --- |
| `name` | string | 必填；英文命令名，包内唯一 | 命令查找键 |
| `layer` | string | 必填；`std` / `usr` / `skill` | 来源分层，排序用 |
| `aliases` | string[] | 必填；中文别名数组，可含多个 | 中文输入匹配；按别名建索引 |
| `system` | string | 必填；枚举：移动、战斗、物品、社交、门派、经济、系统 | 命令分类页 |
| `status` | string | 必填；枚举：已实现、待实现、不做 | 控制可用性；非「已实现」不可直接执行 |
| `blocker` | string | 待实现/不做时必填 | 不可用原因的分组标签 |
| `evidence` | string | 必填；引擎落点或不做理由，含文件与行号 | 详情页依据；排期核对 |
| `params` | string | 必填；参数提示串 | 补全与用法提示 |
| `summary` | string | 必填；一句话中文说明 | 列表副标题 |
| `canonical` | string | 可选；同义归并指向的 `name` | 多个别名共用同一实现时归并 |
| `parent` | string | 可选；父命令 `name` | 子命令挂载（如 `vote/chblk`） |

壳侧执行前先读 `status`：`已实现` 走引擎动作；`待实现` / `不做` 只展示帮助与 `blocker`，不得当作可执行命令。

### 7.3 manifest 与缓存

`content/xkx_help/manifest.json` 字段：`schema_version`（当前 1）、`generated_by`、`source`（`repo/commit/inventory/topics_index/note`）、`counts`（`commands/topics/status/system`）、`files`（各文件 sha256）、`content_hash`（`commands.yaml + topics.yaml` 的 sha256）。

壳按 `content_hash` 做缓存与校验：哈希不一致时视为内容包已更新，应重新加载；不得在壳内手改已生成文件。

### 7.4 边界

- 本包只负责内容与字面契约；`help` 命令入口、输入匹配、渲染与缓存都在玩家壳完成。
- 引擎侧命令动作及其注册仍由 `src/wsim/actions/registry.py` 负责；本包不新增动作、不改 API。
- 新增或调整命令时先改 `tools/xkx_help/data/*.yaml`，再重跑生成器，避免壳侧手写第二份清单。
