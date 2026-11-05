# COMMIT-CONVENTION

> 本文件是提交前的强制检查表，与 [GIT-WORKFLOW.md](GIT-WORKFLOW.md) 和
> [CHANGELOG.md](../CHANGELOG.md) 配套使用；冲突时以 GIT-WORKFLOW 为准，本文件只补充操作细节。

## 1. 四条规定

1. **每次提交必须有一条 CHANGELOG 条目**：同一 commit 内更新 `CHANGELOG.md`，写在文件顶部
   （时间倒序），字段固定为：改了什么、为什么改、验证方式、commit 短哈希。
2. **只用显式 pathspec 暂存**：`git add <file1> <file2> ...`，逐个列出本次改动的文件。
3. **禁止 `git add -A` / `git add .` / `git add -u`**：仓库里常有其他任务的未提交文件、
   运行产物与截图，整目录暂存会把它们混进本次提交。
4. **tag 只增不改**：已推送的 tag 不删除、不重打、不移动；需要修正时新增版本 tag
   （如 `delivery-p3j.1`），并在 CHANGELOG 里写清对应关系。

## 2. 提交前检查表

```bash
git status                     # 只应有本次要提交的文件
git diff -- <本次文件>          # 逐个通读
git diff --cached --stat       # 暂存区必须只包含显式 pathspec
git diff --cached | grep -nE 'sk-|ghp_|github_pat_|AKIA|Bearer [A-Za-z0-9._-]{20,}|(key|token|password)=.+'
```

- [ ] `CHANGELOG.md` 顶部已加本次条目，且“验证方式”写的是**实际跑过的命令与结果**。
- [ ] 暂存区没有 `runs/`、`dist/`、`*.sqlite`、`.env`、截图、令牌或密钥。
- [ ] commit message 前缀正确（`feat/fix/test/docs/chore/revert(scope):`），正文含改了什么 / 为什么 / 验证。
- [ ] 提交后立即 `git push origin HEAD`，并用 `git ls-remote origin refs/heads/master`
      与本地 `git rev-parse HEAD` 对账。

## 3. CHANGELOG 条目模板

```markdown
## [类型] YYYY-MM-DD — 一句话标题

- **改了什么**：文件 / 模块 / 行为；只写事实，不写计划。
- **为什么改**：触发问题、目标、取舍。
- **验证方式**：实际命令与关键输出（例如 `pytest 536 passed, 18 skipped`、
  `selfcheck 4/4 PASS`、CI run URL / 耗时）。数字必须可追溯到 `git log`、
  `docs/RELEASE-NOTES-*.md`、`docs/DELIVERY-TEST-*.md` 或 `reports/`。
- **commit**：`<短哈希>`（推送前可写“见本提交”，推送后用一次 `docs(changelog):` 补记回填）。
```

## 4. 提交粒度

- 一类改动一个 commit：CI、镜像/脚本、文档规范分开提交，不把多个里程碑压进一笔。
- 一个 commit 只做一件事；重构与行为变更分开，便于用 `git revert` 精确回滚。
- 回滚提交用 `revert(scope):`，正文写清回滚原因与被回滚的 commit 短哈希。

## 5. 推送与 tag

```bash
# 普通提交
git push origin HEAD

# 发布 tag（annotated，message 写 commit 短哈希 / content_hash / 验证结论）
git tag -a delivery-p3m -m "commit=abc1234 content_hash=7ec670c1… selfcheck 4/4 PASS"
git push origin --tags
```

- 已推送的 tag **永不改写**：`git push --force --tags`、删除远程 tag、移动 tag 全部禁止。
- tag 与 commit 的对应关系必须在 `CHANGELOG.md` 可查；tag message 不得留空。
- 发布镜像版本号建议与 tag 同名（`tools/build_image.sh <tag>`），回滚时按 tag 定位镜像。

## 6. 反例（禁止）

| 写法 | 为什么禁止 |
| --- | --- |
| `git add -A && git commit -m "update"` | 混入无关文件；message 无法追溯 |
| 一次 commit 同时改 CI、业务代码、CHANGELOG、截图 | 无法单独回滚；审查成本高 |
| 提交里没有 CHANGELOG 条目，事后补 | 违反“每次改动一条记录” |
| `git push --force` / `git tag -f` | 覆盖远程历史与已发布版本 |
| CHANGELOG 里写“优化了”“大幅提升” | 无数据支撑，必须写命令与实测数字 |
