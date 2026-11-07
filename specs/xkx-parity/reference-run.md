# xkx100 原版参照基线：本机实跑记录

> 结论（2026-11-07）：`fluffos/xkx100` **已在本机原生跑通**。FluffOS v2019.20220507 从源码编译为 arm64 Mach-O，mudlib 以 UTF-8 正常加载，telnet 4004 可登录、建号、看房间、移动、查 `score`、触发战斗与 NPC 对话。四条里程碑全部达成，未进入“卡死/换路线”分支。

| 里程碑 | 状态 | 证据 |
| --- | --- | --- |
| 1. FluffOS 编译成功 | **通过** | `fluffos v2019.20220507 (Mac OS X)`，`Mach-O 64-bit executable arm64`，产物 `tools/xkx_ref/src/fluffos-v2019/build/src/driver` |
| 2. 连入并看到首个房间 | **通过** | 建号后进入「南浔镇」，完整房间文本见 §3.2；既有角色重登进入「客店」（无锡），见 §3.3 |
| 3. 战斗与对话 | **通过** | `kill liu mang` 逐行战斗+死亡+鬼门关，见 §3.5；`ask wang wu about 鬼刀` 原始对白见 §3.6 |
| 4. 卡死分支 | **未触发** | 无 |

---

## 1. 环境与版本

| 项 | 值 |
| --- | --- |
| 机器 | macOS 26.5.2（Build 25F84），`uname -m` = `arm64` |
| 工具链 | Apple clang 21.0.0、CMake 4.2.2、GNU Bison 3.8.2 |
| Homebrew 依赖 | `icu4c@76 76.1_1`、`pcre 8.45`、`jemalloc 5.4.0` |
| 驱动 | FluffOS `v2019.20220507`（tag `v2019.20220507`，commit `eda54c5fe59c00c0cecffaf58513eb3d396b9a1c`） |
| 驱动产物 | `tools/xkx_ref/src/fluffos-v2019/build/src/driver`，4,596,136 字节，arm64 |
| 驱动 SHA256 | `b59d7942652eedd3167fd820077fa71a672a5f6a5c9451cd7c4c51c778c64bc1` |
| mudlib | `fluffos/xkx100` commit `c1b87db0014c85514e053b2a149a2efccb9914e3`（2026-10-14） |
| 监听 | 仅 `telnet 4004`（未占用本机 8000/8031/8032/8039 等现有服务） |

驱动版本原始输出：

```text
========================================================================
Full Command Line: ./build/src/driver --version
Boot Time: Sat Nov  7 14:35:36 2026
Version: fluffos v2019.20220507 (Mac OS X)
jemalloc Version: 5.4.0-0-g7a34f18502e7b222724097cdcd499b437d189acc
ICU Version: 76.1
libdw or libbfd is not found, you will only get very limited crash stacktrace.
Core Dump: No, Max FD: 65535.
========================================================================
```

---

## 2. 从零复现

```bash
# 依赖（一次性）
brew install bison icu4c@76 pcre jemalloc

cd /Users/lyinet/projects/XKX/world-sim

# 1) 编译驱动：源码构建 OpenSSL 1.1.1w + FluffOS v2019，并打两个兼容补丁
tools/xkx_ref/build_fluffos_v2019.sh

# 2) 生成 mudlib 工作副本（固定 commit、.lpc→.c、端口改为 4004、建 log/nosave）
tools/xkx_ref/prepare_mudlib.sh

# 3) 启动（前台占用 4004）
tools/xkx_ref/run_ref.sh
```

另开终端登录与抓取：

```bash
telnet 127.0.0.1 4004
# 或脚本化探针（自动完成建号/登录，并把原始字节与去 ANSI 文本写入 JSON）
python3 tools/xkx_ref/probe_mud.py --port 4004 --player-id mytest \
  --commands "look" "score" --record reports/xkx-ref/my-session.json
```

探针说明：英文名必须是纯字母；新号建号的固定回答为中文名「测试侠客」、密码 `xkxref2026`、出生地 `3`（江南六城随机）、天赋 20/20/20、性格 `0`、性别 `m`。这些都在 [probe_mud.py](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_ref/probe_mud.py) 的 `CREATE_RULES` 中。

### 2.1 构建脚本实际做的三处非标准处理（缺一不可）

| 问题 | 原始报错 | 处理 |
| --- | --- | --- |
| v2019 加载 mudlib 只认 `.c` | `The simul_efun file adm/single/simul_efun.c was not loaded.`（仓库里只有 `.lpc`） | `prepare_mudlib.sh` 全量 `.lpc`→`.c`，并把源码中 `inherit "...xxx.lpc"` 的 224 个文件里的 `.lpc"` 字面量改成 `.c"` |
| OpenSSL 3 下 libwebsockets 编不过 | `field has incomplete type 'HMAC_CTX'`（`lws-genhash.h`） | 改为源码构建 OpenSSL **1.1.1w** 静态库，仅给本仓库用，不污染系统 |
| lws 的 feature 探测失败 | `Looking for HMAC_CTX_new - not found`，即便已找到 OpenSSL 1.1.1w | 在 `src/CMakeLists.txt` 强制 `-DLWS_HAVE_HMAC_CTX_new=1 -DLWS_HAVE_EVP_MD_CTX_free=1` |
| lws 用 `-lssl/-lcrypto` 相对链接 | `ld: library 'ssl' not found` | 追加 `link_directories(${OPENSSL_ROOT_DIR}/lib)` |

补丁由 [build_fluffos_v2019.sh](file:///Users/lyinet/projects/XKX/world-sim/tools/xkx_ref/build_fluffos_v2019.sh) 幂等写入，只改被 ignore 的 `src/fluffos-v2019/`，不改仓内业务代码。

---

## 3. 真实输出

原始抓包（含 ANSI、原始 telnet 字节流）保存在：

- [session-firstroom.json](file:///Users/lyinet/projects/XKX/world-sim/reports/xkx-ref/session-firstroom.json)：新建角色、首个房间
- [session-v2019.json](file:///Users/lyinet/projects/XKX/world-sim/reports/xkx-ref/session-v2019.json)：既有角色重登、首个房间（客店）
- [session-status.json](file:///Users/lyinet/projects/XKX/world-sim/reports/xkx-ref/session-status.json)：`score`/`hp`/`i`
- [session-combat.json](file:///Users/lyinet/projects/XKX/world-sim/reports/xkx-ref/session-combat.json)：移动 `go south` + `ask`
- [session-npc.json](file:///Users/lyinet/projects/XKX/world-sim/reports/xkx-ref/session-npc.json)：王五「鬼刀」对白并转入战斗
- [session-combat2.json](file:///Users/lyinet/projects/XKX/world-sim/reports/xkx-ref/session-combat2.json)：完整战斗、死亡、鬼门关

以下为去掉 ANSI 控制码后的终端原文。`> ` 是服务端提示符。

### 3.1 登录横幅（节选，完整版在 JSON）

```text
侠客行一百已运行 八分二十三秒
现在的侠客行时间 戊午年四月九日亥时三刻
现在的 北京 时间 二千零二十六年十一月七日十四时三十二分五十四秒，星期六
现在的 农历 日期 黄帝历四千七百二十三年九月二十九日乙酉
您是侠客行一百自二千零一年一月十四日以来的第一千零十三位访问者。
目前共有 零 位世外高人、零 位江湖儿女在线上，以及 一 位使用者尝试连线中。
您的英文名字：使用 xkxrefb 这个名字将会创造一个新的人物，您确定吗(y/n)？
```

### 3.2 建号流程与新角色首个房间（完整描述）

```text
您的中文名字：请设定您的密码：
请再输入一次您的密码，以确认您没记错：

请选择你的人物出生地：

    参天之树，必有其根，怀山之水，必有其源。
    故乡，是游子梦魂萦绕、辗转反侧的地方，是游子心中最深最沉的牵挂。

    你可以选择出生在下列城市：

    1〖 北京 〗 〖 开封 〗 〖 南阳 〗 〖 灵州 〗 〖 漠北 〗 〖 西夏 〗
    2〖 长安 〗 〖 洛阳 〗 〖 兰州 〗 〖 汉中 〗 〖 襄阳 〗 〖 永登 〗
    3〖 扬州 〗 〖 杭州 〗 〖 苏州 〗 〖 无锡 〗 〖 嘉兴 〗 〖 宜兴 〗
    4〖 福州 〗 〖 泉州 〗 〖 台湾 〗 〖 延平 〗 〖 石梁 〗 〖 澎湖 〗
    5〖 大理 〗 〖 衡阳 〗 〖 江陵 〗 〖 岳阳 〗 〖 佛山 〗 〖 成都 〗

    请输入数字（1~5）选择你想出生的地区范围，具体城市由系统随机选择。

您的出生地是：宜兴
...
你的天赋总点数为80，您请输入您的膂力点数(10到30)：你的天赋点数尚余60点，您请输入您的悟性点数(10 到 30)：你的天赋点数尚余40点，您请输入您的根骨点数(10 到 30)：
膂力[20]， 悟性[20]， 根骨[20]， 身法[20]
您同意这一组天赋吗？
请选择你的人物性格属性：
...
您不选择特定的性格。

您的电子邮件地址：
您要扮演男性(m)的角色或女性(f)的角色？

目前权限：(player)

                     ╭----------------╮
                     ┃  +=江湖公告=+  ┃
                     ╰----------------╯


    □ 南少林开派。请help nanshaolin参看帮助文件。
    □ 泰山派开派。请help taishan参看帮助文件。
    □ 衡山派开派。请help henshan参看帮助文件。
    □ 铁掌帮开派。请help tiezhang参看帮助文件。
    □ 红花会开派。请help honghua、map huijiang参看帮助文件。
    □ 主页照片更新：http://210.34.4.4/xkx100/photo.htm 
南浔镇 - 
    湖州府自来文风甚盛，历代才士辈出，梁时将中国字分为平上去入
四声的沈约，元代书画皆臻极品的赵孟兆都是湖州人氏。当地又以产笔
著名，湖州之笔，徽州之墨，宣城之纸，肇庆端溪之砚，文房四宝，天
下驰名。南浔镇虽是湖州府的一个镇，却比寻常州县还大，镇上富户极
多，著名的富室大族之中有一家姓庄。
    东方的天空渐渐地发白了。
    天色太黑了，你看不清明显的出路。

你上次光临侠客行一百是 Thu Jan  1 08:00:00 1970 从 0 连接的。


> 
```

### 3.3 既有角色的首个房间（无锡·客店，完整描述）

```text
客店 - 
    这是一家价钱低廉的客栈，生意非常兴隆。初次来到无锡的游客大
多选择这里落脚，你可以在这里打听到各地的风土人情。店小二里里外
外忙得团团转，接待着南腔北调的客人。客店的主人从不露面，他究竟
是谁，有各种各样的猜测。墙上挂着一个牌子(paizi)。
    这里明显的出口是 south、north 和 up。
  客店留言板(Board) [ 没有任何贴子 ]
  店小二(Xiao er)

你上次光临侠客行一百是 Thu Jan  1 08:00:00 1970 从 0 连接的。

> 【风闻】有人：“朱熹老夫子在扬州授业颁发学位，你我兄弟也去混张文凭如何？”


店小二笑咪咪地说道：这位小兄弟，欢迎来到侠客行一百。
```

### 3.4 一条移动的完整输出（`go south`，客店→西新街）

```text
西新街 - 
    西新街上的人虽然多得不得了，街面却还干净、整洁。人来人往，
吆喝声、叫卖声汇成一片。你看来川流不息的人群，心里异常兴奋和高
兴，街边的小贩吆喝着小笼馒头和鸡汤馄饨，令你食欲大开。南边的二
泉茶馆原本开在惠山下的真正的二泉旁边，后来为了生意还是搬到这条
街上来了，可每天都是派人专程去二泉用水车拉来泉水沏茶的。路北边
是这条街上生意最好的客栈，进出的都是来自天南地北的人。
    夜幕低垂，满天繁星。
    这里明显的出口是 south、north、east 和 west。
  无赖(Wu lai)
> 
```

命令层同时验证了 `go <方向>`；等价的裸方向 `south` 也可用。

### 3.5 `score` 面板（原文）

```text

┏----------【个人档案】--------┓ 		    天赋
┃                               ┃    悟性：[ 20/ 20]     根骨：[ 20/ 20] 
┃头　　衔：　  【 少  年 】    ┃    膂力：[ 20/ 20]     身法：[ 20/ 20]
┃中文姓名:     测试侠客        ┃    耐力：[???/???]     容貌：[???/???]
┃英文姓名:     xkxref          ┃    灵性：[???/???]     福缘：[???/???]
┃性    别:     男性            ┃    胆识：[???/???]     定力：[???/???]
┃人物性格:     普通            ┗----------------------------------------┓
┃称　　谓：  　普通百姓                                                  ┃
┃你的家乡：  　无锡               你的老婆： 目前还没有                  ┃
┃年　　龄：    十四岁一月         你的生辰： 丙申年十一月十日未时三刻    ┃
┃你的师父：    目前还没有         师门忠诚： 0                           ┃
┃杀生总数：    0       玩家：    0      百姓：    0      小孩：    0     ┃
┃    同门：    0       师长：    0      同帮：    0      长老：    0     ┃
┃被杀总计：    12      横死：    0      叛师：    0      开除：    0     ┃
┃最近死因：    被无赖击死了                                              ┃
┃经    验：    6                  食物： ■■■■■■■■■■■■■■■■                              ┃
┃潜    能：    1                  饮水： ■■■■■■■■■■■■■■■■                              ┃
┃正    气：    0                  <精>： ■■■■■■□□□□□□□□□□                              ┃
┃江湖阅历：    0                  <气>： ■■■■■■□□□□□□□□□□                             ┃
┃江湖威望：    0                  江湖魅力：    0                        ┃
┗------------------------------------------------------------------------┛
 你在侠客行一百里的游戏时间是:5m 

> 
```

### 3.6 NPC 对话原文（`ask wang wu about 鬼刀`，醉仙楼二楼）

```text
你向王五打听有关『鬼刀』的消息。
王五眼中突然放出异样的光芒，颤声说道，看来...你都知道了？
王五大喝一声，叫道，既然如此，老子今天反正豁出去，跟你拼了。
王五使开单刀，招数精奇，沉猛狠辣，和刚才的醉鬼简直判若两人。
看起来王五想杀死你！
王五说道：老子就是鬼刀王五，鬼刀王五就是你老子！纳命来吧！
```

同一条命令立刻把闲聊转成战斗，后续输出：

```text
王五挥动钢刀，斩向你的左脸！
结果「嗤」一声划出一道伤口！
( 你已经陷入半昏迷状态，随时都可能摔倒晕去。 )
王五见势不好，转身要走，被你一把拦在面前。想走？可没那么容易！
【江湖传闻】听说在武夷山一带隐居着一位武林前辈，江湖人士纷纷前往寻访！

你对准王五的左脚用力挥出一拳！
但是王五已有准备，不慌不忙的躲开。

你往王五的左臂狠狠地踢了一脚！
但是被他机灵地躲开了。
王五目不转睛地盯着你的动作，寻找进攻的最佳时机。
```

### 3.7 一场战斗的逐行输出（`kill liu mang`，扬州·十里长街）

```text
你对着流氓喝道：「小王八蛋！明年今日就是你的忌日！」

看起来流氓想杀死你！
> 
你往流氓的两肋狠狠地踢了一脚！
但是流氓身子一侧，闪了开去。

流氓往你的右脸一抓！
结果在你右脸抓出一道细长血痕。
( 你气喘嘘嘘，看起来状况并不太好。 )
你注视着流氓的行动，企图寻找机会出手。

流氓提起拳头往你的后心捶去！
结果你痛哼一声，在你的后心造成一处瘀伤。
( 你受了几处伤，不过似乎并不碍事。 )

你往流氓的小腹一抓！
但是被他机灵地躲开了。
流氓慢慢地移动着脚步，伺机出手。

你往流氓的左肩一抓！
但是被他及时避开。
流氓见你攻击失误，趁机发动攻击！

流氓对准你的左脸用力挥出一拳！
结果在你的左脸造成一处瘀青。
( 你受伤不轻，看起来状况并不太好。 )
流氓注视着你的行动，企图寻找机会出手。
你正盯着流氓的一举一动，随时准备发动攻势。

流氓往你的左腿狠狠地踢了一脚！
结果在你的左腿造成一处瘀青。
( 你似乎十分疲惫，看来需要好好休息了。 )

你对准流氓的右耳用力挥出一拳！
但是和他右耳偏了几寸。
流氓注视着你的行动，企图寻找机会出手。

你挥拳攻击流氓的右臂！
但是被他及时避开。
你一击不中，露出了破绽！

流氓对准你的右腿用力挥出一拳！
结果在你的右腿造成一处瘀青。
( 你受伤不轻，看起来状况并不太好。 )

流氓提起拳头往你的后心捶去！
但是被你机灵地躲开了。
你注视着流氓的行动，企图寻找机会出手。

流氓往你的两肋狠狠地踢了一脚！
结果在你的两肋造成一处瘀青。
( 你气息粗重，动作开始散乱，看来所受的伤着实不轻。 )

流氓对准你的左耳用力挥出一拳！
结果在你的左耳造成一处瘀青。
( 你已经陷入半昏迷状态，随时都可能摔倒晕去。 )

你挥拳攻击流氓的右腿！
但是流氓身子一侧，闪了开去。
你注视着流氓的行动，企图寻找机会出手。

你挥拳攻击流氓的左耳！
但是流氓已有准备，不慌不忙的躲开。
流氓见你攻击失误，趁机发动攻击！

流氓往你的两肋一抓！
结果在你两肋抓出一道细长血痕。
( 你已经陷入半昏迷状态，随时都可能摔倒晕去。 )
流氓缓缓地移动脚步，想要找出你的破绽。

你的眼前一黑，接着什么也不知道了....

【谣言】某人：我看到测试侠客被流氓杀死了。真是好惨。

你倒在地上，挣扎了几下就死了。

鬼门关 - 
    这里就是著名的阴间入口「鬼门关」，在你面前矗立著一座高大的
黑色城楼，许多亡魂正哭哭啼啼地列队前进，因为一进鬼门关就无法再
回阳间了。
    这里明显的出口是 south 和 north。
  白无常(White gargoyle)
```

> 注：新号（经验 0）对 1000 经验的流氓几乎打不中，上面的“只挨打”是真实数值结果，不是脚本丢行。

---

## 4. 失败链与已修问题

| # | 现象 | 证据 | 根因 | 处理 | 验证 |
| --- | --- | --- | --- | --- | --- |
| F1 | 已有 2024 版驱动加载 mudlib 失败 | `boot-v2024.log`: `The simul_efun file adm/single/simul_efun.c was not loaded.` | xkx100 源码全为 `.lpc`，FluffOS 的 `simulate.cc` 写死 `.c` | 工作副本全量改名 `.lpc`→`.c`，并替换 224 个文件中的 `.lpc"` 引用 | v2024 与 v2019 均可启动 |
| F2 | v2019 构建第一步失败 | `error: field has incomplete type 'HMAC_CTX'`（lws-genhash.h） | 仓内 libwebsockets 不兼容 OpenSSL 3 | 源码构建 OpenSSL 1.1.1w（静态、`no-shared no-tests`） | `Found OpenSSL: ...libcrypto.a (1.1.1w)` |
| F3 | 即便用 1.1.1w，CMake 仍探测不到 HMAC | `Looking for HMAC_CTX_new - not found` | lws 的链接型 feature 探测在本机失败 | 强制 `LWS_HAVE_HMAC_CTX_new`、`LWS_HAVE_EVP_MD_CTX_free` | 编译通过 |
| F4 | 链接期找不到 ssl | `ld: library 'ssl' not found` ×3 | lws 用 `-lssl` 相对名 | `link_directories(${OPENSSL_ROOT_DIR}/lib)` | 生成 4.6MB driver |
| F5 | 战斗死亡时刷屏运行时错误 | `Wrong permissions for opening file /log/nosave/KILLRECORD for append.` + `combatd.c 第 1069 行` | 工作副本缺 `log/nosave` 目录 | `prepare_mudlib.sh` 预建该目录 | 第二次战斗死亡输出干净（§3.7） |
| F6 | 未修 F5 前同一角色反复死亡 12 次 | `score`: 被杀总计 12 | `die()` 在写 KILLRECORD 处报错，死亡流程未完成，心跳继续判定 | 修 F5 后死亡正常进入鬼门关；本次基线角色被杀总计 13 | §3.7 输出末尾正常进「鬼门关」 |
| F7 | 夜间房间不显示出口 | `天色太黑了，你看不清明显的出路。` | mudlib 白天/黑夜描述逻辑，非故障 | 不修，作为真实行为记录 | `south/north` 仍可移动，已实测 |

仍未解决/未做的：

1. **websocket 端口未启用**。本次只开 telnet 4004；v2019 的 lws 在 OpenSSL 3 下有兼容问题，基线不需要 wss。
2. **没有 wizard 权限**。所有输出都是普通玩家视角；未用 `goto`/`call` 等巫师命令，也未改 mudlib 数据。
3. **时间/随机性**。房间的“白天/黑夜”追加行、NPC greeting、`random(6)` 出生城市都会变；同一条命令在不同时刻输出可能不同，对拍时需固定游戏内时间或按语义比对。
4. **未做源码级 patch 之外的改动**。mudlib 只做后缀/端口/日志目录处理，未改任何 LPC 逻辑。

---

## 5. 与复刻工程的对接建议

- **文本基线口径**：房间 `short`/`long`、出口列表、`score` 字段顺序、战斗每回合“攻击-结果-受伤程度”三行结构，可直接用 §3 的输出做 golden case。
- **数值基线口径**：新号 20/20/20/20、经验 0；对 1000 exp 的流氓命中率极低；死亡进鬼门关。后续若要更可比的战斗样本，应先用任务/练功把角色练到与目标 NPC 同量级，再固定随机种子或多次采样。
- **可复现抓取**：每次运行 `probe_mud.py` 都会写一份 JSON（含原始字节与去 ANSI 文本），适合作为回归对比的机器可读证据。

## 6. 文件与边界

本次只新增/修改以下范围，未触碰 `src/**`、`ui/**`、`content/**`、`config/**`：

- `tools/xkx_ref/`：`build_fluffos_v2019.sh`、`prepare_mudlib.sh`、`run_ref.sh`、`probe_mud.py`、`.gitignore`
- `reports/xkx-ref/`：6 份会话 JSON（见 §3 顶部链接）
- `specs/xkx-parity/reference-run.md`：本文件

源码与产物（`tools/xkx_ref/src/`、`deps/`、`run/`、`logs/`）全部被 `tools/xkx_ref/.gitignore` 忽略，不入库。

> 说明：`specs/xkx-parity/content-inventory.md` 第 43/551 行仍写着 `fluffos/xkx100` “未实测跑通”。该文件不在本次授权修改范围，故未改动；以本文件的实测结论为准。
