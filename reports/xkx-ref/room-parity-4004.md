# 房间逐字段对拍报告

**本次样本 10 个代表房间，不是全量**（复刻杭州切片共 195 个房间，全量扩样待跑，见「复现命令」中的 `--all`）。

- 房间数：10 / 195
- 原版来源：`port 4004（独立会话，未启停实例；本次从已落盘原文重算）`（mudlib = `/Users/lyinet/projects/XKX/world-sim/tools/xkx_ref/run/mudlib`，与 4004 同一棵树）
- 复刻来源：`/Users/lyinet/projects/XKX/world-sim/content/xkx/rooms.json`
- 采集方式：startroom（独立会话逐房改 startroom 直登；本次只从已落盘原文重算）

## 方法与顺序

- 先跑 10 个代表房间（楼外楼、青石大道、断桥、白堤、孤山、灵隐寺、苏堤、六和塔底、岳坟、净慈寺），工具跑通后再全量。
- 原版侧**逐房改存档 `startroom` 后直接登录并 `look`**，不在原版里走路。原因：原版与复刻的个别出口不一致，用复刻出口图走路会串房（实测把“山洞/六和塔”等错误房间的文本记到别的房间上），把导航噪声当成内容差异。`startroom` 是存档数据字段，只决定登录落点，不参与房间内容生成。
- 采集在 4004 上用临时快照副本探针（4 字母 id，存档与登录存根用完即删）逐房改 `startroom` 直接登录 `look`；不启停实例，不长时间占用。
- 复刻切片内没有 `武庙`（该房在 `/d/city`，不在杭州切片），因此代表房间用杭州地标；这一点是覆盖范围差异，不是工具缺陷。
- 夜间室外房间出口不可见时，`exits` 记 `observable:false`，不计入一致率；描述与物品/NPC 照常比较。

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
| name | 10 | 10 | 100.0% |
| desc | 10 | 10 | 100.0% |
| exits | 10 | 10 | 100.0% |
| items | 10 | 10 | 100.0% |
| npcs | 10 | 9 | 90.0% |

## 差异分类

| 分类 | 数量 |
|------|------|
| npcs_mismatch | 1 |

## 差异清单

| 房间 | 字段 | 说明 |
|------|------|------|
| `/d/hangzhou/duanqiao` | npcs | 原版 {} vs 复刻 {'穷汉': 1} |

## 差异样例

### npcs_mismatch — `/d/hangzhou/duanqiao` (断桥)
```json
{
 "id": "/d/hangzhou/duanqiao",
 "short": "断桥",
 "fields": {
  "name": {
   "match": true,
   "orig": "断桥",
   "repl": "断桥"
  },
  "desc": {
   "match": true
  },
  "exits": {
   "match": true,
   "orig": [
    "northeast",
    "west"
   ],
   "repl": [
    "northeast",
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
   "orig": {},
   "repl": {
    "穷汉": 1
   }
  }
 },
 "status": "differ"
}
```


## 复现命令

所有数字都可由 `tools/xkx_ref/parity_rooms.py` 重跑；`--reparse` 只读已落盘的原始 `look` 文本重算，
不连实例，用于核验解析与统计口径。

```bash
# 4004 现场采集（需先准备临时探针存档；本次已清理）
python3 tools/xkx_ref/parity_rooms.py --limit 10 --by-startroom --no-instance --port 4004 --player xkxpq --tag 4004

# 只重算、不重采集（结果与上面一致）
python3 tools/xkx_ref/parity_rooms.py --limit 10 --reparse

# 全量（复刻杭州切片全部房间）
python3 tools/xkx_ref/parity_rooms.py --all --by-startroom --port 4020

# 4004 对照：只从已落盘原文重算
python3 tools/xkx_ref/parity_rooms.py --limit 10 --reparse --tag 4004 --port 4004 --player xkxpq
```

- 原始 `look` 文本：`reports/xkx-ref/room-parity/raw/`（4020）、`reports/xkx-ref/room-parity/raw-4004/`（4004）。
- 重新在 4004 上采集需要临时探针存档（`--no-instance --port 4004 --player xkxpq --tag 4004`），探针用完即删；本次已清理，报告只保留已落盘原文。