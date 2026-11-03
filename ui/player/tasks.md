# Tasks（当前实现）

- [x] `server.py` 改为薄反向代理：`/api/player/enter|state|action|intents/<id>|messages` 原样转发到 8020
- [x] 删除进程内 `LiveWorld` 与旧写路由；保留静态服务
- [x] `GET /api/health` 暴露 `mode/readonly/upstream/upstream_ok`
- [x] 写动作被拒归一为 HTTP 409 + `reason`/`message` 别名；读接口业务失败 200
- [x] `--readonly` 降级可用：只读回放 + 提案，不写 events.sqlite
- [x] 上游不可达时自动降级并提示；默认仍为 live
- [x] `app.js` 改用 `player` / `player.wallet` / `quest_board` / `tick` / `account_id`
- [x] 对话回复只从 `/messages?since_tick=` 渲染，`details.reply` 仅兜底
- [x] 任务卡显示 `accepted_by_me`，接单后重新拉 state
- [x] 刷新/重启代理按 `account_id` 恢复；清除旧 `ws_player_session`
- [x] 缓存加固：代理 `no-store`，页面 `cache:'no-store'`
- [x] `start.sh` 启动守卫：已有 `events.sqlite` 自动换名；`--reset`/`--reuse`/`--readonly`
- [x] 五步闭环实测（300 文、位置变化、event_seq、messages 回复、任务计数）
- [x] 六步 curl 复现：API 不重置位置；`already_online` 不重置；`leave→enter` 也不回出生点
- [x] 浏览器实测：进入、移动、对话轮询、接任务、刷新保持位置、只读模式提示
- [x] `README.md` 写清只读/可写路径、降级开关、run 清空/换名、原始输出与歧义

## 已知遗留

- `wsim.api` 不 replay `events.sqlite`；重启 API 必然新 genesis（契约待补）。
- `docs/player-api.md` 的 rejected 状态码（200）与验收裁定（409）不一致，需主会话更新契约。
- live 模式无 NPC 详情/世界日志接口，页面按 `present_npcs` 与本地操作记录降级展示。
