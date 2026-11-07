# 边界 stub 与区外引用清单

用于区分“内容包确实缺东西”和“原版本身如此”。本清单里的条目都不是 bug。

数据来源：`rooms.json`（`kind=boundary_stub`）、`items.json` / `npcs.json`（`origin=external`）、
`diagnostics.jsonl`（`I_STUB_EXIT_DROPPED` / `W_BOUNDARY_EXIT` / `W_ASYMMETRIC_EXIT`）。

## 1. 边界 stub：6 条

切片房间有出口指向区外，抽取器把区外目标房间解析成替身，只保留它回到切片的那条出口。

| stub id | 名称 | 切片内入口 | stub 回程 | 可通行 |
| --- | --- | --- | --- | --- |
| `/clone/shop/hangzhou_shop` | 颐香斋 | `/d/hangzhou/majiu` up | down → majiu | 是 |
| `/d/binghuo/qiantang` | 钱塘江畔 | `/d/hangzhou/qiantang` eastdown | westup → qiantang | 是 |
| `/d/meizhuang/shijie` | 孤山石阶 | `/d/hangzhou/gushan` westup | eastdown → gushan | 是 |
| `/d/suzhou/dongmen` | 相门 | `/d/jiaxing/jnroad2` west | east → jnroad2 | 是 |
| `/d/taishan/yidao1` | 大驿道 | `/d/jiaxing/jnroad1` northwest | southeast → jnroad1 | 是 |
| `/d/yanping/xianxialing` | 仙霞岭 | `/d/jiaxing/jnroad4` southup | northdown → jnroad4 | 是 |

当前没有 `status=closed` 的 boundary exit：6 条都有回程，`traversable=true`。
玩家可以进去、看一眼、再走回来；再往外就走不动了。

### stub 被裁剪的区外出口：5 条（这些目标暂时不可达）

| stub | 方向 | 被裁剪的目标 |
| --- | --- | --- |
| `/d/meizhuang/shijie` | north | `/d/meizhuang/xiaolu` |
| `/d/suzhou/dongmen` | west | `/d/suzhou/dongdajie2` |
| `/d/taishan/yidao1` | northeast | `/d/taishan/yidao2` |
| `/d/taishan/yidao1` | west | `/d/taishan/yidao` |
| `/d/yanping/xianxialing` | south | `/d/yanping/erbapu` |

这 5 条在 `diagnostics.jsonl` 里各有一条 `I_STUB_EXIT_DROPPED`。扩区域时按目标 id 补房间即可，不用改引擎。

## 2. 区外引用：25 条

切片内房间的 `objects` 或商店的 `vendor_goods` 引用了区外定义文件。为保证加载不断链，这些对象
已按 `origin=external` 一并抽入 `items.json` / `npcs.json`。

重要：它们**在切片内是可遇见/可购买的**，不是“不可达”。不可达的是它们原本所在的区外地图。
包里只保留静态字段（名称、描述、价格、属性、技能、装备、问话），原文件里的函数行为没有搬。

### 物品 12 条

| id | 名称 | 价格 | 来源 |
| --- | --- | --- | --- |
| `/clone/medicine/vegetable/baiwei` | 白薇 | 1200 | 房间 `/d/hangzhou/dadao1` |
| `/clone/medicine/vegetable/baizhi` | 白芷 | 900 | 房间 `/d/hangzhou/huagang` |
| `/clone/medicine/vegetable/heye` | 荷叶 | 5 | 房间 `/d/hangzhou/suti2` |
| `/d/changan/obj/chairs` | null（原版无 `set_name`） | 2000 | 房间 `/d/shiliang/dongxiangfang` |
| `/d/city/obj/changjian` | 长剑 | 1500 | 商店 `/d/hangzhou/npc/smith` |
| `/d/city/obj/hammer` | 铁锤 | 3 | 商店 `/d/hangzhou/npc/smith` |
| `/d/beijing/obj/tudao` | 屠刀 | 2000 | 商店 `/d/hangzhou/npc/smith` |
| `/d/xingxiu/obj/tiegun` | 铁棍 | 100 | 商店 `/d/hangzhou/npc/smith` |
| `/d/city/obj/gangdao` | 钢刀 | 1000 | 商店 `/d/hangzhou/npc/smith` |
| `/clone/food/jitui` | 烤鸡腿 | 30 | 商店 `/d/jiaxing/npc/xiaoer` |
| `/clone/food/baozi` | 包子 | 50 | 商店 `/d/jiaxing/npc/xiaoer` |
| `/clone/food/liquid/jiudai` | 牛皮酒袋 | 100 | 商店 `/d/jiaxing/npc/xiaoer` |

### NPC 13 条

| id | 名称 | 来源 |
| --- | --- | --- |
| `/d/city/npc/xiaoer` | 店小二 | 房间 `/d/hangzhou/kedian`（`inherit F_VENDOR_SALE`，已按 NPC 收录） |
| `/d/city/npc/zaohongma` | 枣红马 | 房间 `/d/hangzhou/majiu` |
| `/d/city/npc/huangbiaoma` | 黄骠马 | 房间 `/d/hangzhou/majiu` |
| `/d/city/npc/ziliuma` | 紫骝马 | 房间 `/d/hangzhou/majiu` |
| `/clone/misc/mafu` | 马夫 | 房间 `/d/hangzhou/majiu` |
| `/d/mingjiao/npc/chengchaofeng` | 程嘲风 | 房间 `/d/hangzhou/yuelang` |
| `/kungfu/class/heimuya/xiang` | 向问天 | 房间 `/d/jiaxing/chating` |
| `/d/taohua/npc/laoda` | 船老大 | 房间 `/d/jiaxing/jiaxinggang` |
| `/kungfu/class/gumu/limochou` | 李莫愁 | 房间 `/d/jiaxing/nanhu` |
| `/d/village/npc/kid` | 小孩 | 房间 `/d/jiaxing/njeast` |
| `/d/village/npc/girl` | 女孩 | 房间 `/d/jiaxing/njroad2` |
| `/d/village/npc/boy` | 男孩 | 房间 `/d/jiaxing/njroad4` |
| `/kungfu/class/baituo/ouyangfeng1` | 欧阳锋 | 房间 `/d/jiaxing/yanyu3` |

例外：`/d/taohua/npc/laoda`（船老大）原版没有 `long`，`desc=null`。它是 `external`，不进
`coverage.total`；加载器对区外引用缺描述应静默容忍或用通用兜底，不要按切片内容判缺陷。
其余 24 条描述均来自原版（`desc_source=lpc`）。

## 3. 切片内孤立房间：4 条（不可达，但源码如此）

这 4 个房间没有任何出口，也没有任何房间指向它们。原版靠特殊命令/传送进入（如湖面、迷宫底部），
不是抽取丢出口。

| 房间 id | 名称 |
| --- | --- |
| `/d/hangzhou/santan` | 三潭印月 |
| `/d/hangzhou/xihu` | 西湖 |
| `/d/shiliang/huli` | 湖里 |
| `/d/shiliang/midao` | 山洞底 |

加载器不要为它们自动补出口，也不要因为“进不去”报错。

## 4. 单向出口：9 条（原版设计如此）

以下出口在源码里本来就没有回程，`diagnostics.jsonl` 记 `W_ASYMMETRIC_EXIT`。引擎不要自动补回程：

- `/d/jiaxing/mishi` out → `/d/jiaxing/xiaodian`
- `/d/jiaxing/yanyu3` down → `/d/jiaxing/yanyu2`
- `/d/shiliang/andao1` up → `/d/shiliang/xiaoting`
- `/d/shiliang/jinshedong` up → `/d/shiliang/shandong7`
- `/d/shiliang/jinshedong1` west → `/d/shiliang/jinshedong`
- `/d/shiliang/shandong1` east → `/d/shiliang/shandong2`
- `/d/shiliang/shandong2` west → `/d/shiliang/shandong3`
- `/d/shiliang/shandong3` south → `/d/shiliang/shandong4`
- `/d/shiliang/shandong4` north → `/d/shiliang/shandong5`

## 5. 汇总口径

| 类别 | 数量 | 是否可达 | 处理建议 |
| --- | --- | --- | --- |
| boundary stub | 6 | 是（可进可回） | 正常加载 |
| stub 被裁剪区外出口 | 5 | 否 | 扩区域时补 |
| 区外物品引用 | 12 | 是（房间内/商店有售） | 正常加载，忽略 `origin` |
| 区外 NPC 引用 | 13 | 是（房间内可遇） | 正常加载；`laoda` 缺描述可兜底 |
| 孤立房间 | 4 | 否 | 原版特殊入口，勿补出口 |
| 单向出口 | 9 | 是（只去不回） | 不要自动补回程 |
