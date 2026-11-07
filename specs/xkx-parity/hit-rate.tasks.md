# 命中率实测任务分解 v3（已执行）

- [x] T1 构造与校验 MID 快照
  - [x] 复制 `run/snapshots/xkxref.o` → `run/snapshots/xkxmid.o`，改 `combat_exp=6400`、`skills (["unarmed":40,"dodge":40,"parry":40,])`（仅数据，不改 LPC）
    - 取 6400 而非 0：`cmds/skill/practice.c` 允许 40 级的最低经验；exp=0 会让伤害公式把伤害反复除以 3 到 0
  - [x] dry-run 4010：`score` exp=6400、`skills` 三项 40、`i` 无兵器
- [x] T2 采集器 `tools/xkx_ref/measure_hit_rate.py`
  - [x] 独立实例管理（4010、独立 data/workdir、`config-sample.ini`）、快照覆盖、登录
  - [x] 落点用改存档 `startroom` 直登（不改 LPC，不做 44 步走路）
  - [x] 战斗分段：`kill` → 死亡/逃跑/击杀锚点；每条命重启实例并覆盖快照
  - [x] 解析与报告：双方出手/闪避/招架/命中、气值逐回合、伤害档位、Wilson 区间、三次区间
- [x] T3 NEW × 店小二 ×3（n=240，对方命中 88.33%）
- [x] T4 NEW × 流氓 ×3（n=237，对方命中 99.58%）
- [x] T5 NEW × 老虎 ×3（n=152，对方命中 100%，我方 0 出手）
- [x] T6 MID × 店小二 ×3（n=164，对方命中 0.00%）
- [x] T7 MID × 流氓 ×3（n=179，对方命中 7.82%，三次区间 6.67%–8.82%）
- [x] T8 MID × 老虎 ×3（HEAD 复测：n=152，对方命中 100%，我方 0 出手）
- [x] T9 属性采集：`score`/`skills`/`i`/`hp`、天赋、对手 `look`、LPC 数值（标推断）
- [x] T10 4004 确认跑（≤3 分钟、断开会话、未长时间占用）
- [x] T11 写 `reports/xkx-ref/hit-rate.md`（判据句、逐场景表、汇总对照、可观察/推断清单、复现命令、快照 md5、采样构建 sha）
- [x] T11b 更正记录：作废旧复刻老虎 77.2%（旧构建缺 `set_temp apply/*`），改用 HEAD 100.0%；流氓 10.0%→16.1%
- [x] T12 复核：`--report-only` 从落盘原文重算一致；老虎 MID 按 HEAD 重采 3 次
- [x] T13 提交：显式 `git add`（已落盘 txt 用 `-f`）、推 `origin xkx-parity`
- [ ] T14 释放：停采样实例、确认 4004 守护在线

## 运行命令

```bash
python3 tools/xkx_ref/measure_hit_rate.py --tier new --target xiaoer  --runs 3 --min-attacks 50 --max-lives 80 --port 4010 --fight-secs 60
python3 tools/xkx_ref/measure_hit_rate.py --tier new --target liumang --runs 3 --min-attacks 50 --max-lives 80 --port 4010 --fight-secs 60
python3 tools/xkx_ref/measure_hit_rate.py --tier new --target tiger   --runs 3 --min-attacks 50 --max-lives 60 --port 4010 --fight-secs 60
python3 tools/xkx_ref/measure_hit_rate.py --tier mid --target xiaoer  --runs 3 --min-attacks 50 --max-lives 80 --port 4010 --fight-secs 60
python3 tools/xkx_ref/measure_hit_rate.py --tier mid --target liumang --runs 3 --min-attacks 50 --max-lives 80 --port 4010 --fight-secs 60
python3 tools/xkx_ref/measure_hit_rate.py --tier mid --target tiger   --runs 3 --min-attacks 50 --max-lives 60 --port 4010 --fight-secs 60
python3 tools/xkx_ref/measure_hit_rate.py --report-only
```
