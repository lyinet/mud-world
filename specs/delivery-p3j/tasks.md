# delivery-p3j 任务清单

- [x] 1. 核对 HEAD `c999fb6`、工作树干净、`content_hash=7ec670c1…`、`pytest 321 passed, 8 skipped`。
- [x] 2. 新增 `docs/RELEASE-NOTES-p3j.md`（逐条 8 项变化 + 模型/物价/基线口径）。
- [x] 3. 重写 `docs/known-limitations.md`（p3j 版本、新增 4 条限制、321/8 计数）。
- [x] 4. 更新 `docs/QUICKSTART.md` 与 `deploy/README.md`（p3j、`content_hash`、成长线/面板、321/8）。
- [x] 5. 显式 `git add` 上述文档 + `specs/delivery-p3j/`，提交（禁止 `git add -A`）。
- [x] 6. `git archive HEAD` → `/tmp/xkx-p3j-src`，在干净树内 `tools/build_dist.py --version p3j`。
- [x] 7. 产物复制回 `dist/`，计算 tar.gz / zip SHA256，确认 MANIFEST。
- [x] 8. 解包到 `/tmp/xkx-p3j-clean`，跑 `./start.sh`：面板“今天能做什么”、三条成长线、接 1 单并交 1 单、
   开摊成功、账本资金变化 300→310→10。
- [x] 9. 干净包内跑 `pytest`（321 passed / 8 skipped）、`selfcheck.sh`（4/4）、framework（6/6）、
   断线 `degraded`。
- [x] 10. 核验包内必含/必不含清单；写 `docs/DELIVERY-TEST-p3j.md` 并提交。
- [x] 11. 基于最终提交重建包，复跑测试计数与内容核验。
- [x] 12. 打 annotated tag `delivery-p3j`，输出 tag 对象/提交/SHA256/与 p3i 差异。
