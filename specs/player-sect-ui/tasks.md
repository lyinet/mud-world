# 玩家门派面板任务清单

- [x] T1 `ui/player/index.html`：加「门派」tab 与 `#pane-sect`（`sectHint` / `sectBody` / `sectResult` / `#sectModal`），更新 tablist aria-label。
- [x] T2 `ui/player/style.css`：门派名册/操作/二次确认样式；移动端按钮 ≥44px。
- [x] T3 `ui/player/app.js` 状态：新增 `state.sect`，`applyState` 换账号重置；`__XKX.sectState()` 导出只读投影。
- [x] T4 `ui/player/app.js` 只读投影：`sectOf / sectRole / sectBondOf / sectReputation / sectMaxSkill`；亲属关系/结算状态缺口按 spec 显示「关系未记录 / 门级结算状态」。
- [x] T5 `ui/player/app.js` 未入派：立派（800 文、声望 20、差额文案、按钮禁用）、加入（id/驻地输入 + 后无列表接口说明）。
- [x] T6 `ui/player/app.js` 弟子视角：门派/门主/名册/自己欠饷天数，不渲染门主按钮。
- [x] T7 `ui/player/app.js` 门主视角：名册（技能/好感/信任/欠饷/结算）、收徒差距、派俸回显、驱逐二次确认（欠薪债务仍保留）、金库存取上限。
- [x] T8 `ui/player/app.js` 写动作：`sectAction` 在途「处理中…」、13 拒绝码中文、成功文案带金库余额与名册人数；只读模式提案提示；`details.sect` 解包 `{in_sect, sect}`。
- [x] T9 `tools/ui_check.py`：`MockWorld` 门派造场 + `--only sect`（21 项：三角色、空态、13 码、在途禁用、44px）。
- [x] T10 `tools/ui_check_real_sect.py`：真服务 立派 → 收徒 → 派俸；挣钱链路复用；拒绝如实标 `blocked_*`；证据 `reports/w5-sect/`。实跑 25 项 / 0 失败、672.5s。
- [x] T11 回归：`node --check`、`--only sect`（21/21）、全量 `ui_check.py`（176/176）、真服务脚本（25/25）。
- [x] T12 文档与提交：`PLAYER-GUIDE`、`CHANGELOG`，显式 pathspec 提交并推送（`c6c7023`）。
- [x] T13 分三次回报：三种角色视角、真服务脚本、全量回归（见最终回报）。
