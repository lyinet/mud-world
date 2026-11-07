# 房间逐字段对拍报告

**全量 195 个房间**（复刻杭州切片共 195 个房间，全覆盖）。

- 房间数：195 / 195
- 原版来源：`/Users/lyinet/projects/XKX/world-sim/tools/xkx_ref/run/mudlib-parity (config-parity.ini, port 4020)`（mudlib = `/Users/lyinet/projects/XKX/world-sim/tools/xkx_ref/run/mudlib`，与 4004 同一棵树）
- 复刻来源：`/Users/lyinet/projects/XKX/world-sim/content/xkx/rooms.json`
- 采集方式：startroom（逐房改存档 startroom 直接登录 look）

## 方法与顺序

- 先跑 10 个代表房间（楼外楼、青石大道、断桥、白堤、孤山、灵隐寺、苏堤、六和塔底、岳坟、净慈寺），工具跑通后再全量。
- 原版侧**逐房改存档 `startroom` 后直接登录并 `look`**，不在原版里走路。原因：原版与复刻的个别出口不一致，用复刻出口图走路会串房（实测把“山洞/六和塔”等错误房间的文本记到别的房间上），把导航噪声当成内容差异。`startroom` 是存档数据字段，只决定登录落点，不参与房间内容生成。
- 探针实例是 `run/mudlib` 的独立副本（同一棵树 c1b87db、同一驱动），端口 4020，不占用 4004；4004 对照见文末。
- 复刻切片内没有 `武庙`（该房在 `/d/city`，不在杭州切片），因此代表房间用杭州地标；这一点是覆盖范围差异，不是工具缺陷。
- 夜间室外房间出口不可见时，`exits` 记 `observable:false`，不计入一致率；描述与物品/NPC 照常比较。
- 每次登录后用原版源码 `set("short")` 校验落点；实得短名与目标房不一致记为 `landing`（落点异常），该房所有字段标为不可观察、不计入一致率，差异清单单列。

## 归一化规则

- N1 去 ANSI 颜色码
- N2 丢弃提示符与【谣言/风闻/存盘】等频道行
- N3 描述比较折叠换行与缩进（\s+→空，保留全部非空白字符）
- N4 丢弃室外房间的天色/天气描述行（遍历 adm/etc/nature/ 下全部季节天气表：rain/sun/wind 的 desc_msg；跨零点会自动切换）；夜间出口不可观察，不计入 exits 一致率
- N5 物品/NPC 行去数量前缀与英文括号，按中文名多重集合比较
- N6 房间名去首尾空白与 ' - ' 后缀

## 逐字段一致率

| 字段 | 可观察 | 一致 | 一致率 |
|------|--------|------|--------|
| name | 192 | 192 | 100.0% |
| desc | 192 | 191 | 99.5% |
| exits | 192 | 191 | 99.5% |
| items | 192 | 185 | 96.4% |
| npcs | 192 | 171 | 89.1% |

## 差异分类

| 分类 | 数量 |
|------|------|
| desc_extra | 1 |
| exits_mismatch | 1 |
| items_mismatch | 7 |
| landing_mismatch | 3 |
| npcs_mismatch | 21 |

### NPC 差异细分

| 细类 | 房间数 |
|------|--------|
| 数量不同 | 16 |
| 在场/缺失 | 3 |
| 头衔/状态前缀 | 2 |

“头衔/状态前缀”= 原版显示名带门派头衔或「<昏迷不醒>」状态、复刻只存基名；“在场/缺失”= 一侧在场另一侧不在（含共享实例里 NPC 游走/已离开）；“数量不同”= 同名 NPC 两侧 clone 数不一致。

## 差异清单

| 房间 | 字段 | 说明 |
|------|------|------|
| `/d/hangzhou/road6` | npcs | 原版 {'流氓头': 1, '流氓': 1} vs 复刻 {'流氓': 4, '流氓头': 1} |
| `/d/hangzhou/road12` | npcs | 原版 {'绿营清兵': 1, '杭州提督 李可秀': 1} vs 复刻 {'杭州提督 李可秀': 1, '绿营清兵': 4} |
| `/d/hangzhou/huanglongdong` | npcs | 原版 {'毒蛇 <昏迷不醒>': 1} vs 复刻 {'毒蛇': 1} |
| `/d/hangzhou/yuelang` | npcs | 原版 {'绿营会众': 1, '明教青龙坛坛主 明教第三十五代弟子 程嘲风': 1} vs 复刻 {'明教青龙坛坛主 明教第三十五代弟子 程嘲风': 1, '绿营会众': 2} |
| `/d/hangzhou/dadao1` | items | 原版 {} vs 复刻 {'白薇': 1} |
| `/d/hangzhou/kedian` | items | 原版 {'客店留言板(Board)': 1} vs 复刻 {} |
| `/d/hangzhou/guozhuang` | npcs | 原版 {'大狼狗 <昏迷不醒>': 1} vs 复刻 {'大狼狗': 2} |
| `/d/hangzhou/qingbomen` | npcs | 原版 {'女孩': 1} vs 复刻 {'女孩': 2} |
| `/d/hangzhou/majiu` | items | 原版 {'黄骠马': 1} vs 复刻 {} |
| `/d/hangzhou/majiu` | npcs | 原版 {'枣红马': 1, '马夫': 1, '紫骝马': 1} vs 复刻 {'枣红马': 1, '黄骠马': 1, '紫骝马': 1, '马夫': 1} |
| `/d/hangzhou/suti4` | npcs | 原版 {'麻雀': 1, '乌鸦': 1, '武当派第十四代弟子 李沅芷': 1} vs 复刻 {'武当派第十四代弟子 李沅芷': 1, '乌鸦': 1, '麻雀': 2} |
| `/d/hangzhou/dalu1` | npcs | 原版 {'红花会众': 1, '挑夫': 1} vs 复刻 {'挑夫': 1, '红花会众': 2} |
| `/d/hangzhou/yuhuangsj` | npcs | 原版 {'红花会众': 1} vs 复刻 {'红花会众': 2} |
| `/d/hangzhou/liulangqiao` | npcs | 原版 {'麻雀': 1, '乌鸦': 1} vs 复刻 {'麻雀': 2, '乌鸦': 1} |
| `/d/hangzhou/road1` | npcs | 原版 {'武当派第四代弟子 香火道人': 1} vs 复刻 {} |
| `/d/hangzhou/liuzhuang` | npcs | 原版 {'管家': 1, '家丁': 1} vs 复刻 {'管家': 1, '家丁': 2} |
| `/d/hangzhou/huagang` | items | 原版 {} vs 复刻 {'白芷': 1} |
| `/d/hangzhou/tulu1` | npcs | 原版 {'男孩': 1} vs 复刻 {'男孩': 2} |
| `/d/hangzhou/shanlu5` | npcs | 原版 {'采茶女': 1} vs 复刻 {'采茶女': 2} |
| `/d/hangzhou/shanlu2` | npcs | 原版 {'绿营会众': 1} vs 复刻 {'绿营会众': 2} |
| `/d/jiaxing/tieqiang` | npcs | 原版 {'乌鸦': 1} vs 复刻 {'乌鸦': 4} |
| `/d/hangzhou/longjing` | desc | 复刻多 16 字 / 少 0 字 |
| `/d/hangzhou/longjing` | exits | 原版 [] vs 复刻 ['east', 'south'] |
| `/d/hangzhou/longjing` | npcs | 原版 {'蒙面人': 1} vs 复刻 {} |
| `/d/jiaxing/nanhu` | items | 原版 {} vs 复刻 {'白芷': 1} |
| `/d/hangzhou/faxisi` | npcs | 原版 {'进香客': 1, '老和尚': 1} vs 复刻 {'进香客': 2, '老和尚': 1} |
| `/d/hangzhou/shanlu3` | npcs | 原版 {'男孩': 1} vs 复刻 {'男孩': 3} |
| `/d/hangzhou/liuhe1` | landing | 落点异常：期望「六和塔」，实得「武庙」，未按 startroom 落入目标房（该房不计入一致率） |
| `/d/hangzhou/liuhe8` | landing | 落点异常：期望「六和塔」，实得「鬼门关」，未按 startroom 落入目标房（该房不计入一致率） |
| `/d/shiliang/dating` | npcs | 原版 {'温家老大 温方达': 1, '丫环': 1} vs 复刻 {'温家老大 温方达': 1, '丫环': 2} |
| `/d/shiliang/dongxiangfang` | items | 原版 {'锦面折叠长椅': 1} vs 复刻 {'椅子': 1} |
| `/d/shiliang/huli` | landing | 落点异常：期望「湖里」，实得「鬼门关」，未按 startroom 落入目标房（该房不计入一致率） |
| `/d/shiliang/xixiangfang` | items | 原版 {'锦面躺椅': 1} vs 复刻 {'椅子': 1} |

## 差异样例

### npcs_mismatch — `/d/hangzhou/road6` (青石大道)
```json
{
 "id": "/d/hangzhou/road6",
 "short": "青石大道",
 "fields": {
  "name": {
   "match": true,
   "orig": "青石大道",
   "repl": "青石大道"
  },
  "desc": {
   "match": true
  },
  "exits": {
   "match": true,
   "orig": [
    "east",
    "north",
    "south",
    "west"
   ],
   "repl": [
    "east",
    "north",
    "south",
    "west"
   ]
  },
  "items": {
   "match": true,
   "orig": {},
   "repl": {}
  },
  "npcs": {
   "match": false,
   "orig": {
    "流氓头": 1,
    "流氓": 1
   },
   "repl": {
    "流氓": 4,
    "流氓头": 1
   }
  }
 },
 "status": "differ"
}
```

### items_mismatch — `/d/hangzhou/dadao1` (沿湖大道)
```json
{
 "id": "/d/hangzhou/dadao1",
 "short": "沿湖大道",
 "fields": {
  "name": {
   "match": true,
   "orig": "沿湖大道",
   "repl": "沿湖大道"
  },
  "desc": {
   "match": true
  },
  "exits": {
   "match": true,
   "orig": [
    "north",
    "south",
    "west"
   ],
   "repl": [
    "north",
    "south",
    "west"
   ]
  },
  "items": {
   "match": false,
   "orig": {},
   "repl": {
    "白薇": 1
   }
  },
  "npcs": {
   "match": true,
   "orig": {},
   "repl": {}
  }
 },
 "status": "differ"
}
```

### exits_mismatch — `/d/hangzhou/longjing` (龙井)
```json
{
 "id": "/d/hangzhou/longjing",
 "short": "龙井",
 "fields": {
  "name": {
   "match": true,
   "orig": "龙井",
   "repl": "龙井"
  },
  "desc": {
   "match": false,
   "extra_chars": 16,
   "missing_chars": 0,
   "repl_empty": false,
   "samples": [
    {
     "orig": "茶室。往南便回大路。",
     "repl": "茶室。往南便回大路。蒙面人(Mengmianren)"
    }
   ]
  },
  "exits": {
   "match": false,
   "orig": [],
   "repl": [
    "east",
    "south"
   ]
  },
  "items": {
   "match": true,
   "orig": {},
   "repl": {}
  },
  "npcs": {
   "match": false,
   "orig": {
    "蒙面人": 1
   },
   "repl": {}
  }
 },
 "status": "differ"
}
```

### desc_extra — `/d/hangzhou/longjing` (龙井)
```json
{
 "id": "/d/hangzhou/longjing",
 "short": "龙井",
 "fields": {
  "name": {
   "match": true,
   "orig": "龙井",
   "repl": "龙井"
  },
  "desc": {
   "match": false,
   "extra_chars": 16,
   "missing_chars": 0,
   "repl_empty": false,
   "samples": [
    {
     "orig": "茶室。往南便回大路。",
     "repl": "茶室。往南便回大路。蒙面人(Mengmianren)"
    }
   ]
  },
  "exits": {
   "match": false,
   "orig": [],
   "repl": [
    "east",
    "south"
   ]
  },
  "items": {
   "match": true,
   "orig": {},
   "repl": {}
  },
  "npcs": {
   "match": false,
   "orig": {
    "蒙面人": 1
   },
   "repl": {}
  }
 },
 "status": "differ"
}
```

### landing_mismatch — `/d/hangzhou/liuhe1` (六和塔)
```json
{
 "id": "/d/hangzhou/liuhe1",
 "short": "六和塔",
 "status": "landing",
 "landed": "武庙",
 "reason": "落点异常：期望「六和塔」，实得「武庙」，未按 startroom 落入目标房",
 "fields": {
  "name": {
   "match": null,
   "observable": false
  },
  "desc": {
   "match": null,
   "observable": false
  },
  "exits": {
   "match": null,
   "observable": false
  },
  "items": {
   "match": null,
   "observable": false
  },
  "npcs": {
   "match": null,
   "observable": false
  }
 }
}
```


## 复现命令

所有数字都可由 `tools/xkx_ref/parity_rooms.py` 重跑；`--reparse` 只读已落盘的原始 `look` 文本重算，
不连实例，用于核验解析与统计口径。

```bash
# 本报告样本（全量；会启停 4020 实例）
python3 tools/xkx_ref/parity_rooms.py --all --by-startroom --port 4020

# 只重算、不重采集（结果与上面一致）
python3 tools/xkx_ref/parity_rooms.py --all --reparse

# 4004 对照：只从已落盘原文重算
python3 tools/xkx_ref/parity_rooms.py --limit 10 --reparse --tag 4004 --port 4004 --player xkxpq
```

- 原始 `look` 文本：`reports/xkx-ref/room-parity/raw/`（4020）、`reports/xkx-ref/room-parity/raw-4004/`（4004）。
- 重新在 4004 上采集需要临时探针存档（`--no-instance --port 4004 --player xkxpq --tag 4004`），探针用完即删；本次已清理，报告只保留已落盘原文。

## 4004 对照（10 个代表房间，独立会话）

| 字段 | 可观察 | 一致 | 一致率 |
|------|--------|------|--------|
| name | 10 | 10 | 100.0% |
| desc | 10 | 10 | 100.0% |
| exits | 10 | 10 | 100.0% |
| items | 10 | 10 | 100.0% |
| npcs | 10 | 9 | 90.0% |

- 4004 探针：`xkxpq`，来源 `port 4004（独立会话，未启停实例；本次从已落盘原文重算）`
- 与 4020 独立实例的结果逐字段比对，用于证明“同一棵 mudlib 树 + 同一驱动”下两侧一致。
- 探针为快照副本，逐房改 `startroom` 直接登录，登录后先 `get fire` 把自动加载的火折从地面捡走，避免它被当成房间物品；用完即删存档与登录存根，未改 4004 的 LPC。
- 4004 侧差异房间：`/d/hangzhou/duanqiao`
- 其中 npcs 差异属**共享实例的 NPC 在场状态**，不是复刻缺对象：`d/hangzhou/duanqiao.c` 明确 `set("objects", __DIR__"npc/poorman":1)`（穷汉），复刻侧 objects 亦有该 NPC；4004 上该 NPC 当时不在房内（被玩家击杀/移动后未重置），4020 是新起实例所以仍在。