# 玩家门派面板验收清单

- [x] 未入派：立派入口显示 800 文与声望 20，控件旁写「当前 / 需要 / 差多少」；不足时按钮禁用。
- [x] 未入派：加入已有门派入口保留，并注明「暂不可用：后端未提供门派列表接口（无 factions[]）」。
- [x] 弟子视角：门派名、门主、名册、自己的俸禄状态与欠饷天数；不渲染门主专属写按钮。
- [x] 门主视角：名册含技能/好感/信任/欠饷/逐人结算状态；收徒显示好感 30 / 信任 30 / 上限 3 的差额。
- [x] 门主视角：派俸每日一次，已结算后按钮禁用且回显「今日已结算」。
- [x] 门主视角：驱逐有二次确认，弹层静态写明「欠薪债务仍保留」。
- [x] 门主视角：金库显示保留金 100、单次取出上限 500、可提上限；超限禁用并写明上限。
- [x] 13 个拒绝码全部中文：`no_sect / insufficient_funds / insufficient_rep / already_in_sect /
      not_sect_leader / favor_too_low / trust_too_low / sect_full / disciple_not_found /
      withdraw_over_cap / treasury_reserve / insufficient_treasury / already_settled_today`。
- [x] 写动作在途按钮禁用并显示「处理中…」；成功文案带真实金库余额与名册人数。
- [x] 匿名 / 未登录不渲染空壳假数据（提示「登录并进入世界后查看门派。」）。
- [x] 后端没有的字段不造、不 mock：`disciples[].affection/trust`、`disciples[].settled_today`
      与门派列表缺口均显示「暂不可用」并写明缺哪个字段。
- [x] `details.sect` 按真实契约解包 `{in_sect, sect}`，结果条回显真实数字。
- [x] `python3 tools/ui_check.py --only sect` 通过：21/21。
- [x] `python3 tools/ui_check_real_sect.py --speed 4` 真服务通过：25/25、0 失败、672.5s，
      真实完成「立派 → 收徒 → 派俸」。
- [x] `python3 tools/ui_check.py` 全量回归：176/176、0 失败（基线 155 + 门派 21）。
- [x] 移动端 375×812：页面与页签条均无横向溢出，门派按钮 ≥44px。
- [x] `node --check ui/player/app.js` 通过；`docs/PLAYER-GUIDE.md`、`CHANGELOG.md` 已更新。
