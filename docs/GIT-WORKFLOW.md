# GIT-WORKFLOW

> 本文件约束本仓库的提交、推送、tag 与不入库规则。与本文冲突的旧习惯一律以本文为准。
> 配套变更记录见仓库根目录 [CHANGELOG.md](../CHANGELOG.md)。

## 1. 两条硬规则

1. **每次修改都要推送**：只留本地 commit 视为未完成。提交后立即 `git push origin HEAD`。
2. **每次修改都要详细记录**：commit message 写清“改了什么 + 为什么改”，同一提交内更新 `CHANGELOG.md`。

## 2. 提交规范

- **一类改动一个 commit**。功能、修复、测试、文档、打包不要混在一起；大改动拆成可独立验证的若干提交。
- **message 前缀**：`feat(scope):`、`fix(scope):`、`test(scope):`、`docs(scope):`、`chore(scope):`。
  紧急回滚用 `revert(scope):`，并在正文说明回滚原因。
- **正文必须包含**：
  - 改了什么（文件 / 模块 / 行为）；
  - 为什么改（触发问题、目标、取舍）；
  - 验证方式（跑过的命令与关键结果，例如 `pytest 321 passed, 8 skipped`、`framework 6/6`、replay MATCH）。
- **禁止**空泛 message（如 `update`、`fix bug`、`wip`）；禁止把多个里程碑压进一个 commit。
- **CHANGELOG 同提交更新**：同一提交里给 `CHANGELOG.md` 加一条，字段为改了什么 / 为什么 / 验证方式 / commit 短哈希。
  短哈希可在提交后的一次 `docs(changelog):` 补记中回填，但不得长期留空。

示例：

```text
feat(pricing): 供需物价模型接入运行时

- 改了什么：价格由供需 + 库存驱动，单日 ±20% 护栏；新增 GOLDEN_P3I_PRICE 基线。
- 为什么改：静态物价让交易没有信息差，也与库存脱节。
- 验证：pytest 301 passed, 4 skipped；framework 6/6；事件数接线前后 1224 不变。
```

## 3. 推送时机

- **每次 commit 后立即推送**：`git push origin HEAD`（新分支首次用 `git push -u origin HEAD`）。
- **推送后对账**：`git ls-remote origin refs/heads/$(git branch --show-current)` 与本地 `git rev-parse HEAD` 必须一致。
- **禁止**：
  - `git push --force` / `--force-with-lease` 覆盖远程历史；
  - rebase / amend 任何已经推送的 commit；
  - 跳过 hooks（`--no-verify`）绕过推送流程。
- 远程仓库：`origin = https://github.com/lyinet/mud-world`。推送失败时保留错误原文并排查认证/权限，不要盲目重试。

## 4. Tag 规则

- **只用 annotated tag**：`git tag -a delivery-pX -m "<说明>"`；禁止 lightweight tag。
- tag message 至少写：**对应 commit 短哈希、`content_hash`（若适用）、验证结论摘要**。
- 打 tag 后立即推送：`git push origin --tags`。
- **已推送的 tag 永不改写**：不删除、不重打、不移动。需要修正时新增版本 tag（如 `delivery-p3j.1`）。
- 交付版本 tag 与代码 commit 的对应关系必须能在 `CHANGELOG.md` 中查到。

## 5. 不入库清单

以下内容**禁止提交**：

| 项 | 说明 |
| --- | --- |
| `dist/` | 出包产物（`.tar.gz` / `.zip` / `MANIFEST`），由 `tools/build_dist.py` 生成 |
| `.venv/` | 本地虚拟环境 |
| `*.sqlite` / `*.sqlite-journal` | 运行时数据库与二进制副本 |
| 真实 key / `.env` | API key、token；只允许提交 `.env.example` 占位符 |
| 顶层 `runs/` | 本地运行产物，由 `./start.sh` 生成 |

例外：`reports/evidence/runs/` 是交付证据，需要在库里保留供干净 clone 直接回放，
现由 `.gitignore` 白名单放行，不要扩大白名单范围。

> 已知缺口（2026-11-04）：当前 `.gitignore` **没有**忽略 `dist/`，历史 p3f 产物曾被提交进库。
> 本次制度落地未改动 `.gitignore`；后续应补一条 `dist/` 规则并单独提交（历史产物是否清理需另行决定）。

## 6. 本地自动推送钩子

### 6.1 性质

- 安装在 **`.git/hooks/post-commit`**，属于**本地仓库**，**不会随仓库分发**。
- **新克隆必须重新安装**，否则不会自动推送。
- 行为：commit 后在后台执行 `git push origin HEAD`；成功/失败都追加到 **`.git/push.log`**。
  **失败不阻塞提交、不弹窗、不中断工作**，需要人工查看日志后重试。

### 6.2 一行安装

```bash
printf '%s\n' '#!/bin/sh' 'cd "$(git rev-parse --show-toplevel)" 2>/dev/null || exit 0' '[ -n "$(git remote)" ] || exit 0' "nohup sh -c 'GIT_TERMINAL_PROMPT=0 git push origin HEAD >> .git/push.log 2>&1' >/dev/null 2>&1 &" > .git/hooks/post-commit && chmod +x .git/hooks/post-commit
```

### 6.3 验证

```bash
ls -l .git/hooks/post-commit          # 必须可执行
git commit --allow-empty -m "chore(hook): 验证 post-commit 自动推送"
tail -n 5 .git/push.log               # 应出现 push 结果
git ls-remote origin refs/heads/master  # 与本地 git rev-parse HEAD 对比
```

> 钩子只推当前分支（`HEAD`），**不推 tag**；tag 仍需 `git push origin --tags`。
> 空提交仅用于验证钩子，验证后不要留在历史里（`git reset --soft HEAD~1` 或改用真实提交验证）。

### 6.4 卸载

```bash
rm -f .git/hooks/post-commit
```

## 7. 常用对账命令

```bash
git status                     # 只应有预期内的改动
git log --oneline -10          # 最近提交
git ls-remote origin           # 远程分支与 tag
git rev-parse HEAD             # 本地 HEAD
git rev-parse origin/master    # 本地记录的远程 HEAD
git tag -l --format='%(refname:short)|%(creatordate:short)|%(subject)'
```

## 8. 凭证纪律

- **真实 key 只允许出现在未追踪的 `.env`**（已被 `.gitignore` 忽略）。任何会被 git 跟踪的文件
  （文档、报告、测试、示例、`config/*.yaml`）一律使用占位符，例如 `sk-your-key-here`、
  `<你的 key>`、`<REDACTED>`、`fake`。
- **提交前自查**：`git diff --cached` 通读一遍，确认不含 `key=`、`token=`、`password=` 的真实值，
  也不含 `Bearer ` 后跟长串。必要时可用：
  `git diff --cached | grep -nE 'sk-|ghp_|github_pat_|AKIA|Bearer [A-Za-z0-9._-]{20,}|(key|token|password)=.+'`
- **测试用的假 key** 要在命名上自证（如 `sk-test-*`、`sk-very-secret-key`），不得使用真实格式与真实值。
- **发现泄漏后的处置**（与 [SECURITY.md](../SECURITY.md) 第 1 节同一口径）：
  1. **先清理引用**：从当前文件删除原文、改为占位符；`.gitignore` 补规则防止再次入库；
  2. **不改写历史**：改写会移动全部既有 tag（**16 个，均已推送**：15 个 `delivery-*`
     加 `p2b-baseline`），破坏版本与交付里程碑追溯；
  3. **轮换 key 不是当前硬性要求**：仓库保持 private、无新增协作者、无 CI/外部服务获授权拉取时，
     历史凭证按“已知风险 + 风险接受”处理，由持有人接受该风险；
  4. **触发条件**：一旦仓库转为 public、新增协作者（任何读权限）、或任何 CI/外部服务
     被授权拉取该仓库，**必须在放行前完成 key 轮换**，并把完成时间补记到
     `SECURITY.md` 第 1 节“轮换完成记录”。
  5. 文档中不得写入 key 原文，只写提交哈希与文件名。
- 仓库可见性：当前保持 **private**（已知风险 + 触发条件口径）；转 public 前必须完成
  上述第 4 条的轮换与历史清理。
