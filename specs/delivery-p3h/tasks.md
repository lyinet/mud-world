# delivery-p3h 任务清单

1. 复制 A3 两臂审计文件到 `reports/evidence/a3-*`。
2. 显式 `git add` 盲评结果与 A3 证据，提交 `docs(evidence): 入库盲评与评测结果`。
3. 重写 `docs/known-limitations.md`；新增 `docs/RELEASE-NOTES-p3h.md`、`docs/DELIVERY-TEST-p3h.md`。
4. 更新 `deploy/README.md`（p3h）、`docs/QUICKSTART.md`（287 passed、p3h 口径）。
5. `tools/build_dist.py` 的 `TOP_DIRS` 加 `specs`。
6. 提交文档与打包改动（不含 `dist/`）。
7. `git archive HEAD` → `/tmp/xkx-p3h-src`，在干净树内 `build_dist.py --version p3h`。
8. 产物复制回 `dist/`，计算 SHA256。
9. 解包到 `/tmp/xkx-p3h-clean`，实测 `start.sh`、selfcheck、玩家闭环、断线。
10. 核验包内/包外清单，补写 `DELIVERY-TEST-p3h.md` 实测值并提交。
11. 打 tag `delivery-p3h`（轻量 tag 指向最终提交），输出 tag、路径、SHA256、差异清单。
