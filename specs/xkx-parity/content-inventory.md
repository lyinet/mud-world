# 侠客行（xkx）内容侧可行性盘点

> 状态：只读分析稿。未修改 `src/**`、`ui/**`、`tests/**`，仅新增本文档。
> 核查日期：2026-11-07。
> 对象：老板提出的“按现有架构分叉出一个完全模仿 xkx 的可玩版本”，本文只回答**内容侧**能不能搬、搬多少、要多少人工。
> 引擎侧结论见 [engine-feasibility.md](file:///Users/lyinet/projects/XKX/world-sim/specs/xkx-parity/engine-feasibility.md)，本文不重复其分析，只引用其结论。
> 所有数字均为本地 clone 后实测；口径与命令见 §7。凡未实测的项，本文明确写“未实测/查不到”，不给估计数。

---

## 0. 结论先行

**选定参照：`fluffos/xkx100`。**

| 项 | 结果 |
|---|---|
| 仓库 | https://github.com/fluffos/xkx100 |
| 选定理由 | UTF-8、最近仍在维护、5,683 房间为 xkx100 系最全、FluffOS v2019 移植版、源码为可读 `.lpc` |
| License | **无**（GitHub API `license.spdx_id = null`；仓库根目录无 LICENSE/COPYING） |
| 房间 | **5,683**（`d/` 87 个区域 5,576，其他 107） |
| 出口边 | **12,611** 条（5,560 个房间可解析出口，占 97.8%） |
| 物品 | **2,501** 个定义文件（2,135 有价格、2,126 有描述） |
| NPC | **2,385** 个定义（2,148 有描述、1,749 配技能） |
| 技能 | **362** 个定义（内功 38、轻功 35、招式 251、知识 35；315 个附加 perform 脚本） |
| 门派 | **31** 个（336 个掌门/师父 NPC） |
| 任务 | **2,017** 条静态任务 + **71** 条动态任务 + 96 个任务物品 |
| 师门任务 | **查不到成体系的师门任务**：仅 2 个门派小任务脚本（武当守卫、星宿丁春秋） |
| 经济 | 4 种货币；141 个商店 / 779 个货单条目；2,135 件物品有价 |
| 命令 | 300 个命令文件；玩家可用 **153**（std 81 + usr 30 + skill 42）；32 个帮助主题 |
| 存档 | 驱动私有二进制 `.o`（`save_object`），路径 `/data/user/<首字符>/<id>.o` |
| 战斗公式 | `adm/daemons/combatd.lpc`，命中/招架/伤害/经验修正均有明确行号，见 §2.10 |

**一句话判断（内容侧）**：文本类内容（房间描述、物品、NPC、对话）可以机器抽取并大批量搬运；机制类内容（战斗、技能成长、心跳、刷新、存档、战斗命令）无法搬运，只能按公式重写。**内容 1:1 不等于玩法 1:1。**

---

## 1. 候选仓库对比（5 个，均已本地 clone 或经 GitHub API 核实）

| # | 仓库 | Star / Fork | 创建 | 最后提交（本地 `git log`） | GitHub `pushed_at` | License | 驱动 | 编码/文件 | 单机可跑（实测/结论） | 内容完整度（房间 / NPC / 物品 / 技能 / 门派） |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | [mudchina/xkx100](https://github.com/mudchina/xkx100) | 31 / 21 | 2016-02-20 | 2016-02-20 | 2016-02-21 | **none** | 自带 `mudos.exe`（PE32 i386 Windows GUI） | GBK / `.c` | **未实测**：自带驱动为 Windows 32 位，macOS 不能直接运行；未用 Wine 验证 | 5,515 / 2,372 / 未逐项统计 / 未逐项统计 / 未逐项统计 |
| B | [mudchina/xkx2001](https://github.com/mudchina/xkx2001) | 30 / 26 | 2016-02-20 | 2016-02-20 | 2016-02-20 | **none** | 自带 `mudos.exe` + `mudos.dll`（PE32 Windows） | GBK / `.c` | **未实测**：同上，Windows 专用 | 3,714 / 1,270 / 1,759 / 297 / 19 |
| C | **[fluffos/xkx100](https://github.com/fluffos/xkx100)** | **14 / 10** | 2020-05-24 | **2026-10-14**（`c1b87db`） | 2026-11-03 | **none** | **FluffOS v2019**（仓库不带二进制，需自备） | **UTF-8 / `.lpc`** | **未实测跑通**：本机无 FluffOS 驱动（`which driver` 为空）；README 明确“请使用 fluffos v2019 最新版驱动” | **5,683 / 2,385 / 2,501 / 362 / 31** |
| D | [xiongmao86/xkx2001-utf8](https://github.com/xiongmao86/xkx2001-utf8) | 12 / 6 | 2018-06-19 | 2018-07-02 | 2018-07-01 | **none** | 自带 macOS 驱动（Mach-O x86_64） | UTF-8 / `.c` | **实测失败**：`./driver config.xkx` 报 `Library not loaded: /usr/local/opt/pcre/lib/libpcre.1.dylib`；本机 arm64，驱动为 x86_64 | 3,714 / 1,270 / 1,759 / 297 / 19 |
| E | [ly19811105/pkuxkx-utf8](https://github.com/ly19811105/pkuxkx-utf8) | 11 / 17 | 2021-04-23 | 2021-04-09 | 2021-04-09 | **none** | FluffOS v2019（不带二进制） | UTF-8 / `.c` | **未实测**：无驱动；README 只写“使用 fluffos v2019” | **7,198 / 3,452 / 6,227 / 856 / 25**（北大侠客行，内容与 xkx100 不同） |

### 1.1 为什么选 C（`fluffos/xkx100`）

1. **编码可用**：UTF-8，`file` 识别为 UTF-8 text；A/B 为 GBK，D 为 UTF-8 但驱动跑不起来，E 虽 UTF-8 但内容不是 xkx100 系。
2. **维护状态最新**：最后提交 2026-10-14（float truncation 修复），是 5 个候选里唯一 2020 年后仍在改的。
3. **体量最全（xkx100 系）**：5,683 房间 > A 的 5,515 > B/D 的 3,714。
4. **可解析性最好**：`.lpc` 文本、`inherit ROOM/NPC/ITEM` 结构统一，`set("long"/"exits"/"inquiry")` 字面量集中，适合脚本抽取。
5. **驱动现代**：FluffOS v2019，LPC 语法与 `save_object`/`heart_beat` 行为有公开文档，比 MudOS 0.9.20 更容易对照。

### 1.2 License 事实（必须写清）

- **5/5 仓库均无可识别的开源许可证**：GitHub API 返回 `license.spdx_id = null`；本地 `find -iname 'LICENSE*' -o -iname 'COPYING*'` 结果为空。
- `xiongmao86/xkx2001-utf8` 的 `README` 明确写：“这个版本**仅供学习用**，如果你觉得侵犯了你的版权，请联系我删除。”
- 其余仓库 README 只有使用说明（端口、驱动版本、BUG 提示），**没有任何授权/再发行条款**。
- 原版《侠客行》的版权归属与授权链：**查不到**。公开仓库都是“网友转码/整理”，没有原始授权文件。
- 结论：**不存在可依据的再发行许可**。按老板“不考虑合规作为阻断”的前提可以继续，但事实必须记录为“无 license、无授权文本”。

### 1.3 单机可跑性的实测记录

| 仓库 | 尝试 | 结果 |
|---|---|---|
| D `xiongmao86/xkx2001-utf8` | `./driver config.xkx` | **失败**：`Library not loaded: /usr/local/opt/pcre/lib/libpcre.1.dylib`；`file driver` = `Mach-O 64-bit executable x86_64`，本机 `uname -m` = `arm64`；需要 Rosetta + x86_64 版 pcre1，未继续 |
| C `fluffos/xkx100` | `which driver` / `which fluffos` | **无驱动**；README 要求 FluffOS v2019；未编译驱动，因此未实测启动 |
| A/B `mudchina/*` | `file mudos.exe` | **PE32 i386 Windows GUI**；macOS 不能直接跑，未用 Wine 验证 |
| E `pkuxkx-utf8` | 仓库内查驱动 | **不带驱动**；未实测 |

> 因此“是否真能单机跑起来”的诚实答案是：**5 个候选里没有一个在本机被实际跑通**。D 有最明确的失败证据（缺 libpcre.1 + 架构不匹配）；C 需要先自行编译 FluffOS v2019，未做。

---

## 2. 选定仓库 `fluffos/xkx100` 内容盘点

### 2.1 房间与区域

| 指标 | 数值 | 口径 |
|---|---|---|
| 房间定义文件 | **5,683** | `grep -rlF 'inherit ROOM' --include='*.lpc' .` |
| `d/` 下区域目录 | **87** | `find d -maxdepth 1 -mindepth 1 -type d` |
| `d/` 下房间 | **5,576** | 同上 grep，限定 `d/` |
| `d/` 外房间 | **107** | 5,683 − 5,576 |
| 有 `short` | **5,671**（99.8%） | `grep -rlF 'set("short"'` |
| 有 `long` | **5,464**（96.1%） | Python 解析 heredoc + 引号字符串 |
| `long` 平均长度 | **79.9 字**（max 340） | 同上，去换行后计字符 |
| 有可解析 `exits` | **5,560**（97.8%） | `set("exits", ([...]))` 正则 |
| 出口条数 | **12,611** | 上述映射的键值对数 |
| 不同出口目标字符串 | **4,271** | 含 `__DIR__ "xxx"` 相对写法，未归一化 |
| 无出口房间 | **123** | 5,683 − 5,560 |
| 多次赋值 `exits` 的房间 | **4** | 同一文件出现 >1 次 `set("exits"`，需人工看条件分支 |
| 使用 `__DIR__` 的房间 | **5,625** | 路径解析必须按文件所在目录展开 |
| 有 `coor/x` 坐标 | **5,210**（91.7%） | `set("coor/x"`；另有 `coor/y`、`coor/z` |
| `outdoors` | **3,586** | `set("outdoors"` |
| 有 `objects` 刷新 | **2,613** 房间 / **3,726** 条 | `set("objects", ([...]))`，按顶层键值对计 |
| 自定义 `reset()` | **32** | 绝大多数靠 `inherit ROOM` 自动 reset |

**区域房间数（87 个，完整表）**

| 区域 | 房间 | 区域 | 房间 | 区域 | 房间 |
|---|---|---|---|---|---|
| nanshaolin | 220 | dali | 210 | city | 189 |
| shaolin | 167 | beijing | 141 | mingjiao | 125 |
| huanggong | 120 | emei | 119 | hangzhou | 118 |
| yanziwu | 116 | wudang | 114 | quanzhen | 113 |
| xiakedao | 108 | suzhou | 106 | quanzhou | 99 |
| jiangling | 99 | wudujiao | 94 | gumu | 94 |
| chengdu | 94 | kaifeng | 93 | xiangyang | 91 |
| kunlun | 91 | baituo | 82 | huashan | 81 |
| lingxiao | 79 | yueyang | 78 | taishan | 77 |
| taohua | 76 | xingxiu | 74 | tiezhang | 73 |
| wuyi | 69 | henshan | 69 | fuzhou | 68 |
| changan | 67 | wuxi | 66 | lingzhou | 66 |
| 5lt | 66 | luoyang | 62 | shouxihu | 61 |
| heimuya | 61 | lingjiu | 56 | guiyun | 56 |
| binghuo | 56 | guanwai | 52 | songshan | 51 |
| mobei | 51 | gaibang | 51 | yanping | 50 |
| meizhuang | 49 | shiliang | 44 | shenlong | 44 |
| huijiang | 44 | huangshan | 42 | xiaoyao | 41 |
| hasake | 40 | jueqinggu | 38 | xueshan | 37 |
| wuguan | 37 | hanzhong | 36 | qingcheng | 35 |
| changcheng | 35 | hengshan | 34 | jiaxing | 33 |
| heizhao | 33 | gaochang | 33 | yubifeng | 32 |
| huanghe | 31 | foshan | 31 | village | 30 |
| beihai | 29 | wanjiegu | 28 | lanzhou | 28 |
| baihuagu | 28 | taohuacun | 26 | yongdeng | 24 |
| tianlongsi | 24 | taiwan | 24 | yixing | 22 |
| qilian | 22 | xuedao | 21 | npc | 21 |
| wuliang | 20 | jianzhong | 18 | nanyang | 17 |
| death | 17 | wizard | 5 | jinshe | 4 |

> 注：`npc`、`death`、`wizard` 是系统/特殊目录，不是地理区域；真正地理区域 84 个。合计 5,576。

### 2.2 物品

| 指标 | 数值 | 口径 |
|---|---|---|
| 物品定义文件 | **2,501** | 继承 `ITEM/COMBINED_ITEM/MONEY/CLOTH/F_FOOD/F_LIQUID/SWORD/BLADE/MACE/STAFF/WHIP/AXE/HAMMER/DAGGER/FORK/THROWING/HEAD/NECK/ARMOR/SURCOAT/WAIST/WRISTS/SHIELD/FINGER/HANDS/BOOTS/EQUIP` 的并集，排除 `inherit/` 基类 |
| 分布在 `d/` | 1,472 | 内嵌在区域目录的物件 |
| 分布在 `clone/` | 862 | 通用物件；主要子目录：food 155、book 155、weapon 149、cloth 134、medicine 119、flower 38、misc 32、armor 31、jewelry 19、animal 9（合计 841，其余 21 分散在其他子目录） |
| 分布在 `questobj/` | 93 | 任务物品 |
| 其他 | 74 | `kungfu/` 50、`open/` 16、`u/` 7、`cmds/` 1 |
| 有价格 `set("value")` | **2,135**（85.4%） | 正则计数 |
| 有描述 `set("long")` | **2,126**（85.0%） | 同上 |
| 有材料 `set("material")` | **1,461**（58.4%） | 同上 |
| 有重量 `set("weight")` | **74**（3.0%） | 其余重量由材料/基类推导 |
| 装备槽 | **11** 个防具槽 | `include/armor.h:5-15`：head/neck/cloth/armor/surcoat/waist/wrists/shield/finger/hands/boots |
| 武器类型 | **17** 种 | `inherit/weapon/`：axe/blade/club/dagger/fork/hammer/hook/mace/rake/spear/staff/stick/sword/throwing/whip + poisoned_weapon + weapon |
| 货币物件 | **4** | `clone/money/`：coin/silver/gold/1000-cash |

### 2.3 NPC

| 指标 | 数值 | 口径 |
|---|---|---|
| NPC 定义文件 | **2,385** | `grep -rlF 'inherit NPC' --include='*.lpc' .` |
| 有 `set_name` | 2,383 | 99.9% |
| 有 `long`（描述） | **2,148**（90.1%） | 文本可直接搬 |
| 有 `title`（称号） | 589（24.7%） | 如“西夏国征东大将军” |
| 有 `gender` | 2,152（90.2%） | |
| 有 `age` | 2,369（99.3%） | |
| 有 `combat_exp` | 2,311（96.9%） | |
| 有 `max_qi` | 1,311（55.0%） | 其余可能用默认值/继承 |
| 有 `set_skill` | 1,749（73.3%） | 平均每 NPC 技能数未逐项统计 |
| 有 `attitude` | 2,081（87.3%） | peaceful/aggressive 等 |
| 有 `inquiry` | 558（23.4%） | 对话主题来源 |
| 有 `chat_msg` | 373（15.6%） | 闲聊来源 |
| 有 `vendor_goods` | 141（5.9%） | 商店 NPC |
| 掌门/师父文件 | **336** | `grep -rlF 'inherit F_MASTER' kungfu/class` |

### 2.4 技能

| 指标 | 数值 | 口径 |
|---|---|---|
| 技能定义文件 | **362** | `ls kungfu/skill/*.lpc` |
| 技能附加 perform 脚本 | **315** | `kungfu/skill/**/*.lpc` 677 个减去 362 个定义 |
| `type() = martial` | 325 | `grep 'string type()'` |
| `type() = knowledge` | 35 | 读书/知识类 |
| 无 `type()` | 2 | `love-sword.lpc`、`sky-sword.lpc` |
| `martialtype() = force`（内功） | **38** | |
| `martialtype() = dodge`（轻功） | **35** | |
| `martialtype() = skill`（招式/特殊） | **251** | |
| 武技但无 `martialtype()` | 1 | `kungfu/skill/piaoyibu.lpc` |

**`valid_enable()` 的可用方式分布**（一个技能可对应多种用法）：

| usage | 技能数 | usage | 技能数 |
|---|---|---|---|
| strike | 49 | sword | 38 |
| force | 34 | hand | 17 |
| blade | 16 | finger | 14 |
| cuff | 14 | unarmed | 9 |
| staff | 9 | claw | 9 |
| dodge | 7 | whip | 5 |
| throwing | 5 | leg | 4 |
| club | 4 | parry | 3 |
| spear | 2 | hook / hammer / dagger | 各 1 |

> 口径：只统计 `valid_enable(string usage) { return usage == "xxx"; }` 这种单行形式；`if (usage == "xxx") return 1;` 的多分支写法未计入，所以合计 242 < 362，是**下界**而非技能总数。

### 2.5 门派

| 指标 | 数值 |
|---|---|
| 门派目录 | **31** |
| 掌门/师父 NPC | **336** |
| 门派列表 | baituo, dali, emei, gaibang, gumu, heimuya, hengshan, henshan, honghua, huashan, kunlun, lingjiu, lingxiao, mingjiao, murong, nanshaolin, qingcheng, quanzhen, shaolin, shenlong, songshan, taishan, taohua, tiezhang, wudang, wudujiao, xiakedao, xiaoyao, xingxiu, xueshan, yunlong |

> “门派”在 xkx 里是 `kungfu/class/<门派>/` 目录 + `F_MASTER` 师父链；不等于玩家可加入的“阵营”。现有 `content/sects.yaml` 是 3 个门派，需要扩到 31 并补入门规/传承/拜师链。

### 2.6 任务与师门任务

| 指标 | 数值 | 来源 |
|---|---|---|
| 静态任务条目 | **2,017** | `quest/qwlist.lpc` 501 + `quest/qwlist1.lpc` 552 + `quest/qslist.lpc` 964 |
| 动态任务记录 | **71** | `quest/dynamic_quest` 213 行数据 / 3 行一条 |
| 任务物品 | **96** 文件 / 93 个物品 | `questobj/` |
| 动态任务可用房间 | **4,140** | `quest/dynamic_location`（另有 `dynamic_location1` 4,156） |
| 任务脚本 NPC | **4** | `quest/helian.lpc`、`weixiaobao.lpc`、`shan.lpc`、`w.lpc` |
| 奖励公式 | exp = 100 + random(400)；pot = exp / (random(10)+1)；score = 50 + random(50) | `adm/daemons/questd.lpc:47-50` |
| 任务触发方式 | NPC `ask` / `give`，没有独立 `quest` 指令入口 | `cmds/usr/quest.lpc` 只查状态；`cmds/std/task.lpc` 是任务榜 |
| **师门任务** | **查不到成体系实现** | `grep -rl '师门任务'` = **0 文件**；`师门` 仅 13 个文件，多为拜师/叛师文本；门派专属任务仅 2 处：`kungfu/class/wudang/zhike.lpc:256`（`quest=guard_wd`）、`kungfu/class/xingxiu/obj/di.lpc`（丁春秋小任务） |

> 结论：xkx100 **没有现代 MMO 意义上的“师门任务系统”**。有的是“找物/交物”随机任务榜（2,017 条）+ 少量剧情任务。若产品要“师门任务”，需要**新设计**，不是从 xkx 搬。

### 2.7 经济系统

| 指标 | 数值 | 来源 |
|---|---|---|
| 货币 | **4** 种 | `clone/money/` |
| 面值 | 铜钱 1 文；白银 100 文/两；黄金 10,000 文/两；银票 100,000 文/张（=1,000 两） | `coin.lpc` `silver.lpc` `gold.lpc` `1000-cash.lpc` 的 `base_value` |
| 携带重量 | 铜钱 1、白银 37、黄金 37、银票 3 | `base_weight` |
| 有价物品 | **2,135** | `set("value")` |
| 商店 | **141** 个 NPC / **779** 个货单条目 | 解析 `set("vendor_goods", ({...}))` |
| 商店存盘 | `inherit/room/shop.lpc` 的 `query_save_file`/`save` | `inherit/room/shop.lpc:65-88` |
| 交易命令 | buy/sell/list/value 等 | `cmds/std/` 下与 `F_DEALER` 配合 |

### 2.8 命令集与帮助

| 指标 | 数值 | 口径 |
|---|---|---|
| 命令文件总数 | **300** | `find cmds -name '*.lpc'` |
| 玩家可用 | **153** | `cmds/std` 81 + `cmds/usr` 30 + `cmds/skill` 42 |
| 巫师/管理 | 147 | `cmds/wiz` 87 + `cmds/arch` 28 + `cmds/imm` 10 + `cmds/adm` 22 |
| 帮助主题 | **32** | `help/help/topics` 中 `〖...〗` 条目：intro/newbie/rules/setup/wiz-rules/wiz-setup/gift-char/user-level/cmds/map/relation/transport/family/party/skill/weapon/mission/contest/trade/city/house/cookery/medical/pet/wuguan/calamity/book/bug/game/event/hslj/creates |
| 帮助正文 | 仅 `help/help/topics` 1 个文件 | `find help -type f` = 2（topics + 可能索引） |

**玩家命令分层（举例）**

| 层 | 数量 | 代表命令 |
|---|---|---|
| std（通用动作） | 81 | look, go, get, drop, give, wear, wield, unwield, ask, reply, kill, task, team, follow |
| usr（角色面板） | 30 | score, hp, skills, inventory, quest, map, time, who, save, title, wimpy, suicide |
| skill（武学动作） | 42 | learn, practice, study, exercise, dazuo, jingzuo, lian, yanlian, research, yanjiu, enable, prepare, perform, exert, jiali, bei, abandon, apprentice, expell, recruit |

### 2.9 存档与数据结构

| 项 | 事实 | 来源 |
|---|---|---|
| 存档方式 | `save_object(file)` / `restore_object(file)` | `feature/save.lpc:1-16` |
| 玩家存档路径 | `/data/user/<id 首字符>/<id>.o` | `clone/user/user.lpc:31-37`；`include/globals.h:14` `DATA_DIR "/data/"` |
| 扩展名 | `__SAVE_EXTENSION__`（驱动定义为 `.o`） | `feature/save.lpc` 中 `file + __SAVE_EXTENSION__` |
| 格式 | **驱动私有二进制**，非文本、非 JSON | `save_object` 是 FluffOS efun；无公开反序列化文档 |
| NPC 存档 | `inherit/char/npcsave.lpc` 的 `query_save_file` | 该文件第 63 行 |
| 商店存档 | `inherit/room/shop.lpc:65-88` | |
| 世界状态 | 无全局持久化文件；房间靠 `reset()` 重刷 | `config.ini:76` `time to reset : 900` |

> 对我们的事件溯源模型意味着：**不需要迁移玩家存档**（xkx 的 `.o` 读不了）；只需要迁移内容定义。玩家进度由我们自己的 `events.sqlite` 生成。

### 2.10 战斗公式（含出处行号）

全部在 [adm/daemons/combatd.lpc](https://github.com/fluffos/xkx100/blob/master/adm/daemons/combatd.lpc)。

#### 2.10.1 攻防能力值 `skill_power()`（197–242 行）

```text
level = query_skill(skill)
attack:  level += temp("apply/attack")
defense: level += temp("apply/defense")
defense + 轻功: level = level * power_point(dodge)，power_point ∈ [1.0, 1.5]
if level == 0: 返回 combat_exp / 2
level clamp [1, 800]
str = query_str() clamp [10, 200]
dex = query_dex() clamp [10, 200]
power = (level / 6) * level * level
attack  = (power + combat_exp) / 80 * (str + query("str"))
defense = (power + combat_exp) / 80 * (dex + query("dex"))
```

出处：`combatd.lpc:197-242`。属性本身在 `feature/attribute.lpc:10-96`：

| 属性 | 组成 |
|---|---|
| `query_str`（膂力） | `str` + `apply/strength` + max(unarmed/leg/strike/finger/cuff/hand/claw)/10 |
| `query_int`（悟性） | `int` + `apply/intelligence` + literate/10 |
| `query_con`（根骨） | `con` + `apply/constitution` + force/10 |
| `query_dex`（身法） | `dex` + `apply/dexerity` + dodge/10 |
| `query_sta`（耐力） | `sta` + `apply/stamina` + parry/10 |
| `query_spi`（灵性） | `spi` + `apply/spirituality` + max(buddhism/mahayana/lamaism/taoism/...)/10 |
| `query_per`（容貌） | `per` + `apply/personality` |
| `query_cps`（定力） | `cps` + `apply/composure` |

#### 2.10.2 命中判定（328–354 行）

```text
ap = skill_power(me, attack_skill, ATTACK)，clamp [1, 200000000]
dp = skill_power(victim, "dodge", DEFENSE)，clamp [1, 200000000]
if victim->is_busy(): dp /= 3
if random(ap + dp) < dp: 躲避成功（不中）
```

出处：`combatd.lpc:328-354`。命中概率 = `ap/(ap+dp)`；躲闪概率 = `dp/(ap+dp)`。

#### 2.10.3 招架判定（387–406 行）

```text
if victim 有武器: pp = skill_power(victim, "parry", DEFENSE)
else if 攻击者无武器: pp = skill_power(victim, "unarmed", DEFENSE)
else: pp = 0
if 攻击者无武器: pp *= 2
if victim->is_busy(): pp /= 3
pp clamp [1, 200000000]
if random(ap + pp) < pp: 招架成功（不中）
```

出处：`combatd.lpc:387-406`。

#### 2.10.4 伤害结算（436–558 行）

| 步骤 | 规则 | 行号 |
|---|---|---|
| 基础伤害 | 有武器：`apply/damage`；空手：`apply/unarmed_damage` | 436-439 |
| 高伤截断 | `if damage > 300: damage = 300 + random(damage-300)` | 440 |
| 随机平均 | `damage = (damage + random(damage)) / 2` | 441 |
| 招式加成 | `damage += action["damage"] * damage / 200` | 442-445 |
| 护甲减伤 | `defense_factor = apply/armor`；>300 截断；`defense_factor = (x + random(x))/2`；`damage -= defense_factor` | 451-454 |
| 膂力加成 | `damage_bonus = query_str()` | 464 |
| 内功钩子 | `force_skill->hit_ob(me, victim, bonus, jiali)`；`be_hit_ob` | 466-479 |
| 武技钩子 | `martial_skill->hit_ob` / `be_hit_ob` | 487-497 |
| 武器钩子 | `weapon->hit_ob` / `me->hit_ob` | 500-508 |
| 加成平均 | `damage_bonus = (bonus + random(bonus))/2`；`damage += bonus` | 509-513 |
| 经验修正 | `while random(combat_exp_victim) > combat_exp_me: damage -= damage/3; defense_factor /= 2` | 523-528 |
| 空明拳 | `damage > 50 ? damage/5 : 10` | 534-538 |
| 招式威力 | `damage = damage * power_point(martial_skill)` | 546-549 |
| 扣血 | `victim->receive_damage("qi", damage, me)` | 552 |
| 伤势 | `random(damage) > apply/armor` 且击杀意图时 `receive_wound("qi", damage - armor)` | 560-567 |
| 经验/潜能 | 命中方/被打方按 `random(max_qi+qi) < damage` 等条件 +1 exp/+1 potential | 575-591 |

#### 2.10.5 心跳、忙碌与刷新

| 机制 | 事实 | 来源 |
|---|---|---|
| 心跳开启 | `setup()` 里 `set_heart_beat(1)` | `inherit/char/char.lpc:47-51` |
| 心跳周期 | `config.ini:149-150` 的 `heartbeat interval msec : 1000` **被注释、未显式启用**；实际间隔未在本机实测（驱动未跑起来）。FluffOS 默认值为 1,000ms 属公开文档值，未在此实测验证 | `config.ini:149-150` |
| 慢心跳计数 | `tick = 5 + random(10)`，每 `tick--` 到 0 才做条件/回血 | `inherit/char/char.lpc:51,108-109` |
| 心跳内容 | 死亡/昏迷判定、`continue_action()`（忙）、逃跑、`attack()`、NPC `chat()`、`heal_up()` | `inherit/char/char.lpc:57-126` |
| 忙碌 | `is_busy()` → `continue_action()`；战斗动作串行化 | `char.lpc:81-84` |
| 断线超时 | `IDLE_TIMEOUT 1200` 秒 | `include/user.h:13` |
| 对象刷新 | `time to reset : 900` 秒 | `config.ini:76` |
| 清理 | `time to clean up : 600` 秒 | `config.ini:71` |

#### 2.10.6 技能成长（`feature/skill.lpc:172-204`）

```text
improve_skill(skill, amount, weak_mode):
  if 学习者技能数 > spi: amount /= (sizeof(learned) - spi)   // 学太多会变慢
  learned[skill] += amount
  if learned[skill] > (skills[skill] + 1) * (skills[skill] + 1):
      skills[skill] += 1
      learned[skill] = 0
```

配套命令：`learn`（向师父学）、`practice`（练）、`study`（读书）、`exercise/dazuo/jingzuo`（打坐/静坐）、`research/yanjiu`（研究）、`enable/prepare`（激发/准备）、`perform/exert`（招式/内功）。

#### 2.10.7 装备

| 项 | 事实 | 来源 |
|---|---|---|
| 防具槽 | 11 个 | `include/armor.h:5-15` |
| 穿戴状态 | `equipped = "worn" / "wielded" / "second_wield"` | `feature/equip.lpc:37,112,126,131,143` |
| 穿戴检查 | `armor_type` 与性别/年龄限制 | `feature/equip.lpc:23`；`cmds/std/wear.lpc:38-41` |
| 持武规则 | 已有武器时 `wield` **直接拒绝**，不自动替换 | `cmds/std/wield.lpc:16-17` |
| `wield all` | 已有武器时 break | `cmds/std/wield.lpc:19-24` |
| 武器伤害来源 | `weapon_prop/damage` + `skill_type` | `inherit/weapon/blade.lpc` 的 `set("weapon_prop/damage", ...)` |

---

## 3. 数据可得性

### 3.1 内容载体：纯文本，不是编译进驱动

- 全部内容是 `.lpc` 源码（12,192 个 `.lpc` 文件），由驱动在运行时编译；**内容不在驱动二进制里**。
- 驱动只提供 LPC runtime、efun、`save_object` 等能力；内容可独立于驱动读取。
- 唯一不可读的是玩家存档 `.o`（驱动私有二进制），但它不是内容。

### 3.2 能否机器解析成事件溯源模型

**能，但要分三档。**

| 内容 | 可解析度 | 解析方式 | 风险 |
|---|---|---|---|
| 房间 `short` / `long` | 5,671 / 5,464（99.8% / 96.1%） | 正则提取字符串/heredoc | ANSI 颜色码（`HIY`/`NOR`/`\x1b[...]`）要剥离；多行 heredoc |
| 房间 `exits` | 5,560 / 5,683（97.8%），12,611 条 | 解析 `([ "dir": target ])` | `__DIR__ "x"` 需按文件目录展开；4 个房间有多次赋值；123 个无出口需人工 |
| 房间 `objects` | 2,613 房间 / 3,726 条 | 解析映射 | 宏常量（如 `NOSTRUM_DIR`）需 include 展开 |
| 物品 | 2,501 个 | 解析 `set_name/set("long")/set("value")/set("material")` | 名称含颜色码；价格多为 `set("value")`，少数 `base_value` |
| NPC | 2,385 个 | 解析 `set_name/long/title/gender/age/combat_exp/max_qi/set_skill` | NPC 可能 `clone_object` 生成，需区分模板与实例；`set_name` 数组别名 |
| 对话（inquiry） | **2,784 主题**：1,776 字符串 + 959 函数指针 + 49 其他 | 字符串可直接搬；函数指针只能抽 key | 函数体里的回复文本需要人工读代码，不能自动搬 |
| 闲聊（chat_msg） | **1,165 条**：957 字符串 + 208 函数指针 | 字符串可直接搬 | 同上 |
| 技能 | 362 个定义 + 315 perform | 解析名称/type/martialtype/valid_enable | `perform`/`exert` 是代码，只能重写 |
| 任务 | 2,017 静态条目 + 71 动态 + 96 物品 | 解析 mapping/表格文本 | 奖励与完成判定在 `questd.lpc` 代码里 |
| 商店 | 141 个 / 779 货单 | 解析 `vendor_goods` 数组 | 库存/补货在 `F_DEALER` 代码里 |
| 战斗 | 公式可读 | 人工按 §2.10 重写 | 不能“搬代码” |
| 存档 | 不可读 | 不迁移 | 驱动私有 `.o` |

**解析流程（建议）**

1. `clone --depth 1` 选定仓库。
2. 读 `include/*.h` 建宏表（颜色码、路径常量、`__SAVE_EXTENSION__` 等）。
3. 逐文件提取 `create()` 中的字面量块；`__DIR__` 按文件所在目录展开。
4. 归一化房间图：`(from_room, direction, to_room, condition)`；补反向边前先标记“单向”。
5. 去重 NPC 模板与 `clone_object` 实例；对话 key 与文本分开存。
6. 映射到我们的 YAML/事件模型（房间 → `locations`，物品 → `items`，NPC → `npcs`，对话 → 独立表）。

### 3.3 需要落库的记录量

| 实体 | 记录数 | 说明 |
|---|---|---|
| 房间 | **5,683** | 含 107 个非 `d/` 房间 |
| 出口边 | **12,611** | 有向；归一化后建议再算反向边 |
| 物品 | **2,501** | 含 93 任务物品 |
| NPC | **2,385** | 含 336 师父/掌门 |
| NPC 刷新点 | **3,726** | 2,613 个房间 |
| 对话主题 | **2,784** | 其中 1,776 条有直接文本 |
| 闲聊 | **1,165** | 957 条直接文本 |
| 技能 | **362** | 另有 315 个 perform 脚本 |
| 门派 | **31** | 加 336 个师父关系 |
| 静态任务 | **2,017** | 501+552+964 |
| 动态任务 | **71** | 每条 3 字段 |
| 任务物品 | **96** | 93 个是物品 |
| 商店 | **141** | 779 个货单条目 |
| 物品价格 | **2,135** | |
| 帮助主题 | **32** | 纯文本 |

> 粗口径：**约 3.6 万条内容记录**（不含反向出口边；按表内各项相加约 35,800，若把 779 个货单细项也单列则约 36,600）。其中约 2.6 万条（房间 5,683 + 出口 12,611 + 物品 2,501 + NPC 2,385 + 字符串对话 1,776 + 字符串闲聊 957）可由脚本直接生成；959 条函数型对话、315 个 perform、任务奖励/商店补货等机制必须人工改写。

### 3.4 人工整理量粗估（**粗估，不是承诺**）

> 口径：1 人日 = 1 名熟悉本项目引擎的工程师约 6 小时有效工作；不含美术/音频（原版纯文字，无这些资产）。下列数字是按记录数推算的量级，不是排期承诺。

| 工作项 | 粗估人日 | 计算口径 |
|---|---|---|
| 编写抽取脚本（LPC 字面量 + 宏 + `__DIR__`） | 3–5 | 一次性投入 |
| 房间/出口校验 | 8–12 | 5,683 房间，约 5% 需人工（≈284），40 房间/人日 ≈ 7；加 87 区域命名、123 无出口房间 |
| 物品映射 | 5–8 | 2,501 件，约 400 件/人日 ≈ 6.3；加装备槽/材料映射 |
| NPC 映射 | 8–12 | 2,385 个，约 300 个/人日 ≈ 8；加 title/属性/技能 |
| 对话整理 | 28–40 | 1,776 字符串 / 200 ≈ 8.9；959 函数主题 / 40 ≈ 24；合计 ≈ 33 |
| 技能元数据 | 4–6 | 362 / 100 ≈ 3.6 |
| 任务整理 | 5–8 | 2,017 / 400 ≈ 5；加 71 动态、96 物品 |
| 经济/商店 | 3–4 | 141 / 50 ≈ 2.8 |
| 机制重写（战斗/成长/心跳/刷新/存档/死亡/装备/门派） | **40–80** | 按 §2.10 重写，不含调平衡 |
| 集成与验收（golden/容量/回归） | 15–25 | 参考 `docs/CAPACITY.md` 的 100/150/200 席实测 |
| **合计** | **约 120–200 人日** | 不含产品设计、UI、运营 |

**关键比例**（用于判断“搬内容”还是“重写机制”）：

- 房间描述可直接搬 **96.1%**；出口可解析 **97.8%**。
- 物品描述可直接搬 **85.0%**；价格 **85.4%**。
- NPC 描述可直接搬 **90.1%**。
- 对话主题可直接搬 **63.8%**（1,776/2,784）；闲聊 **82.1%**（957/1,165）。
- 战斗/技能成长/心跳/刷新/存档：**0% 可搬代码**，只能按公式重写。

---

## 4. “完全一样”能做到什么程度

### 4.1 可以 1:1 搬（内容层，文本与数值）

| 类别 | 可搬内容 | 数量 | 注意 |
|---|---|---|---|
| 房间文本 | `short`、`long`、区域名 | 5,671 / 5,464 | 剥离 ANSI 颜色码；补齐 219 个无 `long` 房间 |
| 房间拓扑 | 出口方向与目标 | 5,560 房间 / 12,611 边 | `__DIR__` 归一化；123 个无出口房间人工处理 |
| 物品 | 名称、描述、价格、材料 | 2,501 / 2,126 / 2,135 / 1,461 | 名称含颜色码；重量仅 74 件显式 |
| NPC | 名称、描述、称号、性别、年龄 | 2,385 / 2,148 / 589 / 2,152 / 2,369 | 别名数组、`set_name` 颜色 |
| 对话 | inquiry 字符串回复 | 1,776 主题 | 函数型 959 条只能搬 key |
| 闲聊 | chat_msg 字符串 | 957 条 | 函数型 208 条要重写 |
| 任务 | 2,017 静态条目 + 71 动态记录 | 2,017 + 71 | 奖励公式要按 `questd.lpc` 重写 |
| 商店 | 货单条目 | 141 店 / 779 条 | 库存/补货逻辑要重写 |
| 技能 | 名称、分类、学习前置文本 | 362 | 成长与 perform 不能搬 |
| 门派 | 31 个门派名与师父链 | 31 / 336 | 拜师/叛师规则要重写 |
| 货币 | 4 种货币名与面值 | 4 | 兑换/携带重量要重写 |
| 帮助 | 32 个主题标题与正文 | 32 | 正文只有 topics 一个文件 |

### 4.2 必须重写（机制层，xkx 代码不能直接用）

| 机制 | 原因 | 出处 |
|---|---|---|
| 战斗 | 我们的世界没有 `hp/attack/defense`，`PLAYER_VERBS` 无 `attack/kill`；xkx 是实时回合制 | `combatd.lpc:197-591`；`src/wsim/actions/player.py` 的 `PLAYER_VERBS` |
| 技能成长 | 我们是 `skills: float(0..1)`；xkx 是整数等级 + `learned` 平方门槛 + `spi` 惩罚 | `feature/skill.lpc:172-204` |
| 人物属性 | 无 `str/int/con/dex/sta/spi/per/cps` 与 `apply/*` 临时加成 | `feature/attribute.lpc:10-96` |
| 心跳 | xkx 1 秒 `heart_beat` + `tick 5+random(10)` + busy；我们 1 tick = 10 分钟 | `char.lpc:57-135`；`src/wsim/core/clock.py` |
| 刷新/reset | xkx 900 秒对象 reset；我们靠 schedule/生成 | `config.ini:76`；`adm/daemons` 的 reset 机制 |
| 存档 | xkx `save_object` 二进制；我们 `events.sqlite` 事件溯源 | `feature/save.lpc` |
| 指令解析 | xkx `add_action` + 别名 + 中文参数；我们是 JSON Intent 白名单 | `cmds/`；`src/wsim/api/session.py` |
| 经济 | xkx 商店存盘/补货/货币兑换；我们是 `accounts` + `shop_price` | `inherit/room/shop.lpc`；`src/wsim/actions/commerce.py` |
| 师门任务 | xkx 无成体系系统，只有 2 个门派小任务；需要新设计 | §2.6 |
| 死亡/昏迷/复活 | xkx `die()`/`unconcious()`/`receive_wound`；我们无死亡流程 | `char.lpc:65-78`；`combatd.lpc:560-567` |
| 装备加成 | xkx `apply/armor`、`apply/damage`、双持；我们 `equipped` 无加成 | `feature/equip.lpc`；`src/wsim/world/state.py` 的 `Entity.equipped` |

### 4.3 驱动层独占、我们复刻不了

| 驱动能力 | xkx 用法 | 我们的替代 |
|---|---|---|
| LPC runtime/compiler | 全部内容以 `.lpc` 运行时编译 | 内容改为 YAML，启动时加载 |
| `heart_beat` 调度 | 每个活体 1 秒心跳 | tick 调度器（10 分钟/tick） |
| `call_out` 定时器 | 延迟动作、刷新、任务计时 | `ScheduleItem` + tick 事件 |
| `add_action` 命令注册 | 中文动词即时解析 | `Intent` + `PLAYER_VERBS` 白名单 |
| `save_object` 序列化 | 二进制 `.o` 存档 | `events.sqlite` append-only |
| `message_vision` / ANSI | 房间广播与颜色 | WebSocket/HTTP 投影 + 前端渲染 |
| `shadow` / `call_other` | 技能钩子、状态覆盖 | Effect 系统，无对象影子 |
| `reset()` 对象回收 | 房间/怪物定时重生 | 显式 `spawn` 事件 + schedule |
| 双端口 GBK/UTF-8 | 5555 GBK / 6666 UTF-8 / 8888 WS | 单一 UTF-8 API |
| wizard/ed/OLC | 巫师在线编辑 | 管理端（当前项目无 OLC） |

### 4.4 与现有引擎的衔接（只引用，不重复分析）

[engine-feasibility.md](file:///Users/lyinet/projects/XKX/world-sim/specs/xkx-parity/engine-feasibility.md) 的结论是“**有条件可行**”，推荐路线 A′：同一套引擎 + xkx 内容包，不推翻架构。本文为其提供内容侧数字：

| 维度 | xkx100 | 现有 `world-sim` | 差距量级 |
|---|---|---|---|
| 地点 | 5,683 | `content/locations.yaml` **8**（引擎评估口径 9，含运行时项） | 约 700× |
| 物品 | 2,501 | `content/items.yaml` **40**（引擎口径 41） | 约 60× |
| NPC | 2,385 | `content/npcs.yaml` **30**（引擎口径 31） | 约 80× |
| 技能 | 362 | `Entity.skills` 为 0..1 浮点，无整数等级 | 结构缺口 |
| 门派 | 31 | `content/sects.yaml` **3** | 约 10× |
| 任务 | 2,017 + 71 | `content/quests.yaml` **27** | 约 75× |
| 指令 | 153 玩家命令 | `content/verbs.yaml` **24** | 约 6× |
| 存档 | 二进制 `.o` | `events.sqlite` | 不可互操作 |
| 时间 | 1 秒心跳 / 900 秒 reset | 6 tick/小时 / 144 tick/日 | 机制冲突 |

引擎侧已列出的硬冲突（tick 粒度、无 `hp/attack/defense`、单写者一次动作一条事务、千房间 BFS/内存/容量 100 席安全线）在 `engine-feasibility.md` §0、§1.3、§1.4、§4.1 有实测数据，本文不重复。

---

## 5. 查不到的事项与尝试记录

| 事项 | 结论 | 我试了什么 |
|---|---|---|
| xkx100 原始授权/许可证 | **查不到** | GitHub API `license` 字段；仓库根 `LICENSE*/COPYING*`；README 全文 |
| 原版《侠客行》版权归属/授权链 | **查不到** | 5 个仓库 README、GitHub 仓库描述、无任何版权声明文件 |
| `fluffos/xkx100` 在本机跑通 | **未跑通（未实测启动）** | `which driver`/`which fluffos` 均为空；README 要求 FluffOS v2019；未编译驱动 |
| `xiongmao86/xkx2001-utf8` 在本机跑通 | **失败** | `./driver config.xkx` → `Library not loaded: /usr/local/opt/pcre/lib/libpcre.1.dylib`；`uname -m` = arm64；`file driver` = Mach-O x86_64 |
| `mudchina/*` 在 macOS 跑通 | **未实测** | `file mudos.exe`/`mudos.dll` = PE32 i386 Windows；未安装 Wine |
| 师门任务数量 | **查不到成体系系统** | `grep -rl '师门任务'` = 0；`师门` = 13 文件；门派目录内 `quest` 仅 2 处 |
| 玩家存档 `.o` 的字段结构 | **查不到** | `save_object` 是驱动 efun；无公开格式文档；未反编译驱动 |
| 在线运营数据/玩家数 | **查不到** | 仓库无运营数据；未找到公开统计 |
| 每个 NPC 的平均技能数 | **未逐项统计** | 只统计了“有 `set_skill` 的 NPC 数”=1,749；未求平均 |
| 物品总重/总价分布 | **未逐项统计** | 只统计了有 `weight`/`value` 的文件数 |

---

## 6. 对我们现有内容模型的具体缺口

| 我们的文件 | 现状 | 搬 xkx 前必须补 |
|---|---|---|
| [content/locations.yaml](file:///Users/lyinet/projects/XKX/world-sim/content/locations.yaml) | 8 个地点；无长描述、无方向名、无区域 | `description`、`exits[{direction,to}]`、`area`、`outdoors`、`coords` |
| [content/items.yaml](file:///Users/lyinet/projects/XKX/world-sim/content/items.yaml) | 40 个物品；无描述/重量/装备槽 | `description`、`weight`、`material`、`slot`、`weapon_type` |
| [content/npcs.yaml](file:///Users/lyinet/projects/XKX/world-sim/content/npcs.yaml) | 30 个 NPC；无 hp/攻防/属性 | `attrs`、`max_qi`、`combat_exp`、`skills` 整数等级、`spawn` |
| [content/verbs.yaml](file:///Users/lyinet/projects/XKX/world-sim/content/verbs.yaml) | 24 个动词；无 look/ask/wield/learn 等 | 153 个玩家命令的分层映射（先做高频子集） |
| [content/quests.yaml](file:///Users/lyinet/projects/XKX/world-sim/content/quests.yaml) | 27 个模板；无 `category: sect` | 任务分类、师门任务（需新设计） |
| [content/sects.yaml](file:///Users/lyinet/projects/XKX/world-sim/content/sects.yaml) | 3 个门派 | 31 个门派 + 336 师父/传承链 |
| `src/wsim/world/state.py` 的 `Entity` | 有 `skills: float`、`equipped`，无 `hp/attack/defense/attrs/exp` | 见 `engine-feasibility.md` §1.3；这是引擎改动，不在本文范围 |

> 以上只列“要搬内容必须先有的字段”，不代表现在要改代码。

---

## 7. 来源与复现命令

### 7.1 候选元数据（GitHub API，2026-11-07）

```bash
for r in mudchina/xkx100 mudchina/xkx2001 fluffos/xkx100 \
         xiongmao86/xkx2001-utf8 ly19811105/pkuxkx-utf8; do
  curl -s "https://api.github.com/repos/$r" | python3 -c "
import sys,json; d=json.load(sys.stdin)
print(d['full_name'], d['stargazers_count'], d['forks_count'],
      d['created_at'], d['pushed_at'], (d.get('license') or {}).get('spdx_id'))"
done
```

### 7.2 选定仓库 clone

```bash
cd /tmp/xkx-research
git clone --depth 1 https://github.com/fluffos/xkx100.git fluffos-xkx100
git -C fluffos-xkx100 log -1 --format='%H %ad %s' --date=short
# c1b87db 2026-10-14 Merge pull request #9 from fluffos/fix/float-truncation
```

### 7.3 计数命令

```bash
cd /tmp/xkx-research/fluffos-xkx100

# 房间 / NPC
grep -rlF 'inherit ROOM' --include='*.lpc' . | wc -l      # 5683
grep -rlF 'inherit NPC'  --include='*.lpc' . | wc -l      # 2385

# 区域
find d -maxdepth 1 -mindepth 1 -type d | wc -l            # 87
for d in d/*/; do
  echo "$(basename "$d") $(grep -rlF 'inherit ROOM' --include='*.lpc' "$d" | wc -l)"
done | sort -k2 -rn

# 技能 / 门派 / 命令 / 帮助
ls kungfu/skill/*.lpc | wc -l                              # 362
ls kungfu/class | wc -l                                    # 31
find cmds -name '*.lpc' | wc -l                            # 300
for d in cmds/*/; do echo "$(basename "$d") $(find "$d" -name '*.lpc' | wc -l)"; done
grep -c '〖' help/help/topics                              # 32

# 物品 / 价格
grep -rlF 'set("value"' --include='*.lpc' . | wc -l        # 2264（含非物品）
# 物品并集见 §7.4 Python

# 对话 / 闲聊
grep -rlF 'set("inquiry"' --include='*.lpc' . | wc -l      # 561 files
grep -rlF 'set("chat_msg"' --include='*.lpc' . | wc -l     # 373 files
```

### 7.4 关键解析脚本（房间/对话/物品）

```python
# 房间：long / exits / coords
import re, pathlib, statistics
rooms=[]
for p in pathlib.Path('.').rglob('*.lpc'):
    t=p.read_text(errors='ignore')
    if re.search(r'inherit\s+ROOM', t): rooms.append((p,t))
has_long=0; longs=[]; has_exits=0; exits=0; targets=set(); coords=0; outdoors=0
for p,t in rooms:
    m=re.search(r'set\("long",\s*@([A-Z]+)\n(.*?)\n\1\s*\)', t, re.S)
    if m: has_long+=1; longs.append(len(m.group(2).replace('\n','')))
    else:
        m=re.search(r'set\("long",\s*"((?:[^"\\]|\\.)*)"', t)
        if m: has_long+=1; longs.append(len(m.group(1)))
    m=re.search(r'set\("exits",\s*\(\[(.*?)\]\)', t, re.S)
    if m:
        has_exits+=1
        pairs=re.findall(r'"([^"]+)"\s*:\s*([^,\n]+)', m.group(1))
        exits+=len(pairs)
        for k,v in pairs: targets.add(v.strip())
    if re.search(r'set\("coor/x"', t): coords+=1
    if re.search(r'set\("outdoors"', t): outdoors+=1
print(len(rooms), has_long, round(statistics.mean(longs),1), has_exits, exits, len(targets), coords, outdoors)
# 5683 5464 79.9 5560 12611 4271 5210 3586
```

```python
# 对话：inquiry 主题的字符串/函数比例
import re, pathlib
pat=re.compile(r'set\(\s*"inquiry"\s*,\s*\(\[(.*?)\]\)\s*\)', re.S)
strv=func=other=topics=0
for p in pathlib.Path('.').rglob('*.lpc'):
    for body in pat.findall(p.read_text(errors='ignore')):
        for k,v in re.findall(r'"([^"]*)"\s*:\s*([^,\n]+)', body):
            topics+=1
            if v.strip().startswith('(:'): func+=1
            elif v.strip().startswith('"'): strv+=1
            else: other+=1
print(topics, strv, func, other)
# 2784 1776 959 49
```

```python
# 物品并集（排除 inherit/ 基类）
import re, pathlib
bases={'ITEM','COMBINED_ITEM','MONEY','CLOTH','F_FOOD','F_LIQUID','SWORD','BLADE','MACE','STAFF','WHIP','AXE','HAMMER','PIN','DAGGER','FORK','BRUSH','BOW','THROWING','HEAD','NECK','ARMOR','SURCOAT','WAIST','WRISTS','SHIELD','FINGER','HANDS','BOOTS','EQUIP'}
items=set()
for p in pathlib.Path('.').rglob('*.lpc'):
    if str(p).startswith(('inherit/','include/')): continue
    inh=set(re.findall(r'^inherit\s+([A-Za-z_]+)', p.read_text(errors='ignore'), re.M))
    if inh & bases: items.add(str(p))
print(len(items))   # 2501
```

### 7.5 代码/数据文件索引

| 用途 | 路径（相对 `fluffos/xkx100`） | 链接 |
|---|---|---|
| 战斗公式 | `adm/daemons/combatd.lpc:197-242, 328-354, 387-406, 436-558, 560-591` | https://github.com/fluffos/xkx100/blob/master/adm/daemons/combatd.lpc |
| 属性 | `feature/attribute.lpc:10-96` | https://github.com/fluffos/xkx100/blob/master/feature/attribute.lpc |
| 技能成长 | `feature/skill.lpc:172-204` | https://github.com/fluffos/xkx100/blob/master/feature/skill.lpc |
| 心跳 | `inherit/char/char.lpc:47-135` | https://github.com/fluffos/xkx100/blob/master/inherit/char/char.lpc |
| 超时 | `include/user.h:13` | https://github.com/fluffos/xkx100/blob/master/include/user.h |
| 刷新 | `config.ini:71,76,149-150` | https://github.com/fluffos/xkx100/blob/master/config.ini |
| 存档 | `feature/save.lpc:1-16`；`clone/user/user.lpc:31-37` | https://github.com/fluffos/xkx100/blob/master/feature/save.lpc |
| 装备 | `include/armor.h:5-15`；`feature/equip.lpc`；`cmds/std/wield.lpc:16-24` | https://github.com/fluffos/xkx100/blob/master/include/armor.h |
| 对话 | `cmds/std/ask.lpc:87` | https://github.com/fluffos/xkx100/blob/master/cmds/std/ask.lpc |
| 移动 | `cmds/std/go.lpc:96,342` | https://github.com/fluffos/xkx100/blob/master/cmds/std/go.lpc |
| 任务 | `quest/qwlist.lpc`、`qwlist1.lpc`、`qslist.lpc`、`dynamic_quest`、`adm/daemons/questd.lpc:36-105` | https://github.com/fluffos/xkx100/blob/master/adm/daemons/questd.lpc |
| 货币 | `clone/money/{coin,silver,gold,1000-cash}.lpc` | https://github.com/fluffos/xkx100/tree/master/clone/money |
| 商店 | `inherit/room/shop.lpc:65-88` | https://github.com/fluffos/xkx100/blob/master/inherit/room/shop.lpc |
| 帮助 | `help/help/topics` | https://github.com/fluffos/xkx100/blob/master/help/help/topics |

---

## 8. 给老板的三句话

1. **内容可以搬，而且量很大**：5,683 房间、2,501 物品、2,385 NPC、2,784 对话主题、362 技能、31 门派、2,017+71 任务，全部是可读 `.lpc` 文本。
2. **“完全一样”只能做到内容层的 1:1**：房间文本 96%、出口 98%、物品描述 85%、NPC 描述 90%、对话文本 64% 可直接搬；战斗、技能成长、心跳、刷新、存档、装备加成、师门任务必须按公式/规则重写，代码搬不了。
3. **成本不在“搬”，在“重写 + 校验”**：粗估 120–200 人日，其中机制重写 40–80；另外 5 个候选仓库**全部没有 LICENSE**，xkx2001 的 README 还明确“仅供学习用”，合规事实不能当作不存在。
