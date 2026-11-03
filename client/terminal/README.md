# 经典 MUD 终端客户端（client/terminal/）

只用 Python 标准库（`http.server` + 单文件 HTML/JS），浏览器打开是黑底绿字的终端。
**这是只读外壳：世界仍由主引擎驱动，终端不推进时间、不改世界状态、不写事件日志。**

## 结论

**能做**

- 黑底绿字终端界面，UTF-8 中文正常显示；列宽按“中文 2 列”计算，中英混排对齐。
- 7 条命令：`look` / `go` / `who` / `status` / `quests` / `talk` / `help`。
- `look`：当前地点、出口、在场人物（姓名 + 职业）、可见物品（附近商铺货架合计）。
- `go <地点>`：按内容包 `exits` 校验相邻关系，只移动**客户端坐标**。
- `who`：全世界在线人物及所在地点（按地点分组的三列表格）。
- `status`：当前角色（访客）的钱、物品、目标；未登记项会如实写“未登记”，不编造。
- `quests`：任务板，显示发布人所在地点、是否可接、是否需同行、截止日、报酬与一句可执行提示。
- `talk <人名> <一句话>`：只走 `world.propose_intent`，返回“已提交（intent_id/outbox 序号）”或“被拒 + 原因”。
- 所有错误（地点不存在、不相邻、NPC 不在场、NPC 未找到、run 目录缺失、未知命令）都只回一句人话，不抛栈。

**不能做 / 明确不做**

- 不推进世界时间，不产生事件，不写 `events.sqlite`。
- 不直接读写 `events.sqlite`；世界状态经 MCP gateway 的 `read` 权限取快照，事件投影只读 `events.jsonl`。
- `talk` **不伪造 NPC 回应**：提案只停在内存 `outbox`，本客户端不绑定 Writer，不会被落地。
- `go` 不移动世界里的角色；访客 `player:visitor` 未在内容包里注册，所以 `status` 的钱/物品/目标显示“未登记”。
- 不连真实模型、不联网、不引 CDN、不装第三方包。

## 启动

在 `world-sim/` 目录下：

```bash
python3 client/terminal/server.py --run runs/p3a-clean-72d --port 8010
```

打开 <http://127.0.0.1:8010/>，`Ctrl+C` 停止。

可选参数：

- `--day N`：只回放到第 N 天结束（默认整段 run）。下面的示例用 `--day 5`，好让任务板上有在挂的单。
- `--start market`：客户端初始地点（默认 `market`，不存在时取地点表第一个）。
- `--host 127.0.0.1`：监听地址。

run 目录缺失 / 缺文件 / JSON 坏掉时，服务仍会启动，页面和每条命令都会显示“世界不可用 + 提示”，不会白屏或抛栈。

## 命令示例（实际输出）

以下输出采自：

```bash
python3 client/terminal/server.py --run runs/p3a-clean-72d --port 8010 --day 5
```

> 说明：如果按整段 run 回放（不加 `--day`），`runs/p3a-clean-72d` 在第 72 天结束时任务板上已没有挂出的单
> （全部完成或过期），`quests` 会如实显示“当前没有挂出的任务”。示例用 `--day 5` 是为了看到真实的在挂任务卡。

### 1. `look`

```text
【集市】
世界时间：第 5 天 23:50（tick 719）
出口：书院、客栈、药铺、铁匠铺、茶棚
在场人物（9）：
  严守一  （庙祝）
  卓不凡  （镖师）
  卫长庚  （更夫）
  孟九娘  （媒婆）
  宋鹤年  （掌柜）
  常半夏  （游医）
  谢停云  （师爷）
  阮青蚨  （货郎）
  阿茗    （茶博士）
可见物品（商铺货架）：粗布（一匹）×2000、炊饼×2000、阳春面×2000、生丝（一两）×2000、糙米（一斗）×2000
```

### 2. `who`

```text
在线人物（31 人，世界时间 第 5 天 23:50）：
  位置        姓名        职业
  ----------  ----------  --------
  书院        曹半仙      算命先生
  书院        白露生      学究
  书院        金满仓      掌柜
  书院        陆九斤      脚夫
  书院        韩杏林      药童
  城隍庙      方寸心      抄经人
  客栈        卢杏花      菜农
  客栈        罗轻絮      绣娘
  客栈        花解语      说书人
  客栈        蒋听雨      香客
  客栈        阮小螺      渔娘
  河渡        崔铁衣      捕快
  河渡        石敢当      石匠
  河渡        秦望川      船夫
  河渡        顾青崖      游医
  药铺        柳三娘      绣娘
  药铺        段五斗      屠户
  药铺        温小满      酒保
  药铺        苏合香      掌柜
  铁匠铺      裴无咎      镖师
  铁匠铺      邢破军      捕快
  镖局        沈砚舟      铁匠
  集市        严守一      庙祝
  集市        卓不凡      镖师
  集市        卫长庚      更夫
  集市        孟九娘      媒婆
  集市        宋鹤年      掌柜
  集市        常半夏      游医
  集市        谢停云      师爷
  集市        阮青蚨      货郎
  集市        阿茗        茶博士
```

### 3. `status`

```text
角色：访客（player:visitor，客户端视角；世界状态中未注册）
位置：集市（客户端本地坐标）
世界时间：第 5 天 23:50（tick 719）
钱：未登记（世界账本中没有你的钱包账户）
物品：未登记（世界状态里没有你的随身物品）
目标：无（世界状态里没有你的目标）
待落地提案：0 条（仅在本客户端内存 outbox，未写入世界）
```

### 4. `quests`

```text
任务板（世界时间 第 5 天 23:50）：挂出 10 · 已被接 0 · 已完成 0 · 失败 0 · 过期 17
[可接] 药铺分药（commission_herb_sorting）
     发布人：韩杏林 @ 书院   同行：否   截止：第 6 天   报酬：14 文
     目标：药铺 / 韩杏林
     提示：先去书院找韩杏林接单
[可接] 铁匠铺代工（commission_stonework）
     发布人：石敢当 @ 河渡   同行：否   截止：第 6 天   报酬：12 文
     目标：铁匠铺 / 石敢当
     提示：先去河渡找石敢当接单
[可接] 送书院口信（errand_academy_notice）
     发布人：严守一 @ 集市   同行：否   截止：第 6 天   报酬：5 文
     目标：书院 / 白露生
     提示：先去集市找严守一接单
[可接] 送一匹绸样（errand_silk_sample）
     发布人：柳三娘 @ 药铺   同行：否   截止：第 6 天   报酬：6 文
     目标：药铺 / 苏合香
     提示：先去药铺找柳三娘接单
[可接] 短镖送铁器（escort_iron_short）
     发布人：裴无咎 @ 铁匠铺   同行：否   截止：第 6 天   报酬：12 文
     目标：集市
     提示：先去铁匠铺找裴无咎接单
[可接] 打听书院讲期（inquiry_academy_lecture）
     发布人：曹半仙 @ 书院   同行：是   截止：第 6 天   报酬：8 文
     目标：书院 / 白露生
     提示：先去书院找曹半仙接单，接单时需与白露生同行
[可接] 打听罗家婚事（inquiry_luo_marriage）
     发布人：孟九娘 @ 集市   同行：是   截止：第 6 天   报酬：6 文
     目标：集市 / 罗轻絮
     提示：先去集市找孟九娘接单，接单时需与罗轻絮同行
[可接] 采购木炭（procure_charcoal）
     发布人：宋鹤年 @ 集市   同行：否   截止：第 6 天   报酬：10 文
     目标：河渡 / 宋鹤年
     提示：先去集市找宋鹤年接单
[可接] 采购参片（procure_ginseng）
     发布人：苏合香 @ 药铺   同行：否   截止：第 6 天   报酬：8 文
     目标：药铺 / 苏合香
     提示：先去药铺找苏合香接单
[可接] 护药进城隍庙（escort_medicine_temple）
     发布人：卓不凡 @ 集市   同行：否   截止：第 8 天   报酬：18 文
     目标：城隍庙
     提示：先去集市找卓不凡接单
```

### 5. 错误输入：`go 镖局`

```text
走不过去：集市 不直接通往 镖局。
从集市能走：书院、客栈、药铺、铁匠铺、茶棚。
```

### 6. `go 书院` + `talk 白露生 你什么时候开讲？`

```text
你从集市走到书院。
出口：集市、城隍庙
（客户端坐标已更新；世界里的 NPC、事件日志与账本没有变化。）

已提交：你把话带给了白露生（书院）。
提案 #0（intent_id=outbox:0，tick=719），网关返回 accepted=true。
世界状态未改变（state_unchanged=true）；本客户端不生成 NPC 回应，也不写事件日志。
这条提案只停在本客户端内存 outbox，需要主引擎的 Writer 接纳才会落地。
```

### 7. 被拒示例：`talk 阿茗 今天这壶茶是第几泡？`（人在集市，你在书院）

```text
阿茗不在书院，现在在集市。
先 go 集市 再说话。
```

### 8. `help`（简）

```text
世界审计器 · 经典终端客户端（只读外壳）
  look              看当前地点、在场人物与可见物品
  go <地点>         移动到相邻地点（只改客户端坐标，不动世界）
  who               列出全世界在线人物及所在地点
  status            当前角色状态：钱、物品、目标
  quests            任务板：发布人地点、是否可接、截止与报酬
  talk <人名> <话>  以访客身份提交一条 talk 提案（不写世界）
  help              显示这份帮助
```

## 只读证明

### 1. 操作前后 run 目录逐文件哈希不变

对 `runs/p3a-clean-72d` 的 14 个文件（含 `events.sqlite` / `-wal` / `-shm`）逐文件做 SHA-256，
再对“路径 + 大小 + 哈希”清单做一次聚合哈希；执行 `look / who / status / quests / go / talk / look / help`
前后完全一致：

```text
files 14 tree_sha256 61b97bb0ac1f5e24ec5da94c1f7a7a3888f9fbf453162496cfa0d4198e2eb18f
files 14 tree_sha256 61b97bb0ac1f5e24ec5da94c1f7a7a3888f9fbf453162496cfa0d4198e2eb18f
IDENTICAL True
```

### 2. 整段 72 天回放与 `world_hash.txt` 一致

```text
events 13904  tick 10367  truncated False
state_hash      236deb4779fc…（与 world_hash.txt 相同）
state_hash_match True
```

`--day N` 是截断回放，此时不做哈希比对，页头会显示“未比对（截断回放）”，不冒充 MATCH。

### 3. 代码层的只读约束

- 只打开 `summary.json` / `events.jsonl` / `world_hash.txt` 读文本，从不打开 `events.sqlite`。
- `MCPGateway` 只注册 `read` 与 `propose_intent` 两个身份，不注册 `admin_apply`，且 `writer=None`。
- `AuditLog(None)` 审计只留内存，不写文件。
- 模块顶部 `sys.dont_write_bytecode = True`，不往 `src/` 或本目录写 `__pycache__`。
- 不启动子进程、不联网。

## 依赖了哪些现有接口

| 依赖 | 用途 | 权限/方式 |
|---|---|---|
| `MCPGateway.call(..., "world.read_snapshot", {})` | `look` / `who` / `status` / `quests` / `talk` 前校验 | `read` |
| `MCPGateway.call(..., "world.propose_intent", {"intent": ...})` | `talk` 提案 | `propose_intent`，`writer=None`，只进内存 `outbox` |
| `wsim.mcp.audit.AuditLog` | 网关审计 | 仅内存 |
| `wsim.world.state.WorldState` | 事件投影容器 | 只读 |
| `wsim.world.effects.apply_effects` + `wsim.core.models.Effect` | 用 `events.jsonl` 的 effects 重建投影 | 只读 |
| `wsim.world.hash.state_hash` | 与 `world_hash.txt` 比对 | 只读 |
| `runs/<run>/summary.json` | run 元信息、days | 只读 |
| `runs/<run>/events.jsonl` | 只读事件投影（整段 13904 条；`--day 5` 时 582 条） | 只读 |
| `runs/<run>/world_hash.txt` | 状态哈希比对 | 只读 |
| `content/locations.yaml` + `content/additions/locations.yaml` | 地点名、出口、相邻校验 | 只读 |
| `content/items.yaml` + `content/additions/items.yaml` | 物品中文名 | 只读 |
| `content/quests.yaml` | 任务模板中文名 | 只读 |

没有依赖、也没有调用 `ui/adapter.py` 的 `dialogue()`（那条路径会用 Writer 落地并改写 `events.sqlite`，不符合本客户端的只读约束）。

## 文件

```text
client/terminal/
├── server.py     HTTP 服务、/api/health、/api/cmd、静态页
├── runview.py    只读 run 投影 + MCP gateway（read / propose_intent）
├── commands.py   命令解析、中文列宽排版、错误文案
├── index.html    黑底绿字终端界面（内联 CSS/JS，无框架）
└── README.md
```

## 边界（硬约束对齐）

- 本目录之外的文件一律未修改；`git status` 中 `client/` 是唯一新增项。
- 没有 `git add / commit / tag / branch / checkout / stash`。
- `runs/` 只读，未复制、未写入；无需 `/tmp` 副本。
- 不通过 run 以外的路径读写世界；不触碰 `src/`、`ui/`、`config/`、`tests/`、`content/`、`sim/`、`tools/`、`reports/` 的内容。
