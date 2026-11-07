# 房间对拍任务分解

- [x] T1 建独立探针实例 `run/mudlib-parity` + `config-parity.ini`（端口 4020）与探针快照（startroom=/d/hangzhou/duanqiao、autoload 火折）
- [x] T2 写 `tools/xkx_ref/parity_rooms.py`：登录、逐房改 `startroom` 直登、`look` 抓取、字段解析（另有按复刻出口图走路的 `collect`，全量会串房，不作为口径）
- [x] T3 解析器：name/desc/exits/items/npcs + N1-N6 归一化 + 昼夜/夜间不可观察标记
- [x] T4 复刻侧读取 `content/xkx/*.json`，解析 objects 到中文名
- [x] T5 差异分类与字符级 diff（多字/少字样例）
- [x] T6 10 个代表房间试跑并人工核验（`--limit 10 --by-startroom`）
- [ ] T7 全量 195 房采集，更新同一份 `room-parity.json` / `room-parity.md`（`--all --by-startroom`）
- [x] T8 4004 上 10 房对照跑（临时快照副本探针 `xkxpq`，独立会话，用完即删）
- [x] T9 提交：显式 `git add`、中文说明、推 `origin xkx-parity`（不 force、不 rebase 他人改动）
- [ ] T10 释放：停探针实例，确认 4004 守护在线

# 依赖

- T7 依赖 T1-T6。
- 与命中率采集并行，互不共用实例/端口（对拍 4020，命中率 4010）。
