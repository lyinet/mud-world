#!/usr/bin/env python3
"""生成 xkx 帮助内容包与命令覆盖矩阵。

设计要点：
- 命令清单以 tools/xkx_help/inventory.txt 为冻结口径（153 条）；
- 中文说明/别名/状态/依据来自 data/commands.yaml 与 data/topics.yaml（人工撰写）；
- 指定 --src 时校验源码清单与冻结口径一致，防止源漂移；
- 输出不含时间戳，同一输入重复运行应逐字节一致，便于 git diff。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
TOOL_DIR = Path(__file__).resolve().parent
DATA_DIR = TOOL_DIR / "data"
INVENTORY = TOOL_DIR / "inventory.txt"
TOPICS_INDEX = TOOL_DIR / "topics_index.txt"
SOURCE_JSON = TOOL_DIR / "source.json"

DEFAULT_OUT = ROOT / "content" / "xkx_help"
DEFAULT_SPEC = ROOT / "specs" / "xkx-parity" / "help-coverage.md"

SYSTEMS = ("移动", "战斗", "物品", "社交", "门派", "经济", "系统")
STATUSES = ("已实现", "待实现", "不做")
TOPIC_STATUSES = ("开放", "部分开放", "未开放", "管理")
TOPIC_CATEGORIES = ("入门", "规则", "设置", "管理", "角色", "系统", "社交", "移动", "门派", "战斗", "任务", "经济", "生活")
LAYER_ORDER = {"std": 0, "usr": 1, "skill": 2}

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LPC_TOKEN_RE = re.compile(r"\$[A-Za-z]|@HELP|write\s*\(")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

class LiteralStr(str):
    """按 | 块文本输出，保证帮助正文在 YAML 中可读且逐字节稳定。"""


class Dumper(yaml.SafeDumper):
    """让列表在父键下缩进，输出更接近仓库现有 YAML 风格。"""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> Any:
        return super().increase_indent(flow, False)


def _literal_representer(dumper: yaml.SafeDumper, data: LiteralStr) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


Dumper.add_representer(LiteralStr, _literal_representer)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def dump_yaml(payload: dict[str, Any], header: str) -> str:
    body = yaml.dump(
        payload,
        Dumper=Dumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=10000,
        indent=2,
    )
    return header.rstrip() + "\n\n" + body


def dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def read_inventory(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        layer, name = line.split("\t", 1)
        if layer not in LAYER_ORDER:
            raise ValueError(f"{path}: 未知命令层 {layer!r}")
        rows.append({"layer": layer, "name": name})
    return rows


def read_topics_index(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        topic_id, title = line.split("\t", 1)
        rows.append({"id": topic_id, "title": title})
    return rows


def scan_source_inventory(src: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for layer in ("std", "usr", "skill"):
        root = src / "cmds" / layer
        if not root.is_dir():
            raise ValueError(f"源码缺少目录：{root}")
        for path in sorted(root.rglob("*.lpc")):
            name = path.relative_to(root).with_suffix("").as_posix()
            rows.append({"layer": layer, "name": name})
    return rows


def scan_source_topics(src: Path) -> list[dict[str, str]]:
    path = src / "help" / "help" / "topics"
    if not path.is_file():
        raise ValueError(f"源码缺少主题索引：{path}")
    text = ANSI_RE.sub("", path.read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    for part in re.split(r"〖", text)[1:]:
        match = re.match(r"([a-zA-Z0-9-]+)\s*〗([^│\n]*)", part)
        if match:
            rows.append({"id": match.group(1), "title": match.group(2).strip()})
    return rows


def source_commit(src: Path | None, frozen: dict[str, Any]) -> str:
    if src is None:
        return str(frozen["commit"])
    try:
        out = subprocess.run(
            ["git", "-C", str(src), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return str(frozen["commit"])


def assert_same(label: str, actual: list[dict[str, str]], frozen: list[dict[str, str]]) -> None:
    if actual == frozen:
        return
    actual_keys = [(r.get("layer", ""), r.get("name", r.get("id", ""))) for r in actual]
    frozen_keys = [(r.get("layer", ""), r.get("name", r.get("id", ""))) for r in frozen]
    missing = [k for k in frozen_keys if k not in actual_keys]
    extra = [k for k in actual_keys if k not in frozen_keys]
    raise ValueError(
        f"{label}与冻结口径不一致：源码 {len(actual)} 条，冻结 {len(frozen)} 条；"
        f"缺失={missing[:8]}，多出={extra[:8]}"
    )


def load_authored_commands() -> list[dict[str, Any]]:
    payload = yaml.safe_load((DATA_DIR / "commands.yaml").read_text(encoding="utf-8"))
    return list(payload["commands"])


def load_authored_topics() -> list[dict[str, Any]]:
    payload = yaml.safe_load((DATA_DIR / "topics.yaml").read_text(encoding="utf-8"))
    return list(payload["topics"])


def validate(
    commands: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    inventory: list[dict[str, str]],
    index: list[dict[str, str]],
) -> None:
    errors: list[str] = []

    expected = [(r["layer"], r["name"]) for r in inventory]
    if len(expected) != 153:
        errors.append(f"冻结命令口径应为 153 条，实际 {len(expected)} 条")
    seen: dict[str, dict[str, Any]] = {}
    for entry in commands:
        name = entry.get("name")
        if not name or not isinstance(name, str):
            errors.append(f"命令条目缺少 name：{entry!r}")
            continue
        if name in seen:
            errors.append(f"命令重复：{name}")
        seen[name] = entry
    if set(seen) != {n for _, n in expected}:
        missing = sorted({n for _, n in expected} - set(seen))
        extra = sorted(set(seen) - {n for _, n in expected})
        errors.append(f"命令集合不一致：缺失={missing[:10]}，多出={extra[:10]}")

    alias_to_name: dict[str, str] = {}
    for layer, name in expected:
        entry = seen.get(name)
        if entry is None:
            continue
        if entry.get("layer") != layer:
            errors.append(f"{name}: layer 应为 {layer}，实际 {entry.get('layer')}")
        aliases = entry.get("aliases")
        if not isinstance(aliases, list) or not aliases:
            errors.append(f"{name}: aliases 必须是非空列表")
        else:
            for alias in aliases:
                if not isinstance(alias, str) or not CJK_RE.search(alias):
                    errors.append(f"{name}: 中文别名不合法：{alias!r}")
                previous = alias_to_name.get(alias)
                if previous and previous != name:
                    left = seen.get(previous, {})
                    right = entry
                    same_chain = (
                        right.get("canonical") == previous
                        or left.get("canonical") == name
                        or (right.get("canonical") and right.get("canonical") == left.get("canonical"))
                    )
                    if not same_chain:
                        errors.append(f"中文别名重复：{alias} 同时属于 {previous} 与 {name}")
                alias_to_name[alias] = name
        params = entry.get("params")
        if not isinstance(params, str) or not params.strip() or "\n" in params:
            errors.append(f"{name}: params 必须是一行非空文本")
        summary = entry.get("summary")
        if not isinstance(summary, str) or len(summary.strip()) < 8 or not CJK_RE.search(summary):
            errors.append(f"{name}: summary 必须是中文一句话说明")
        elif len(summary) > 90:
            errors.append(f"{name}: summary 超过 90 字（{len(summary)}）")
        system = entry.get("system")
        if system not in SYSTEMS:
            errors.append(f"{name}: system 不在 {SYSTEMS}：{system!r}")
        status = entry.get("status")
        if status not in STATUSES:
            errors.append(f"{name}: status 不在 {STATUSES}：{status!r}")
        evidence = entry.get("evidence")
        if not isinstance(evidence, str) or len(evidence.strip()) < 10:
            errors.append(f"{name}: evidence 过短或为空")
        elif ANSI_RE.search(evidence) or LPC_TOKEN_RE.search(evidence):
            errors.append(f"{name}: evidence 含 ANSI/LPC 残留")
        if status in ("待实现", "不做") and not entry.get("blocker"):
            errors.append(f"{name}: {status} 必须给 blocker 分组")
        if status == "已实现" and not any(
            token in str(evidence) for token in ("src/", "GET /api/", "content/")
        ):
            errors.append(f"{name}: 已实现必须给出可核对的文件/接口落点")
        canonical = entry.get("canonical")
        if canonical:
            target = seen.get(canonical)
            if target is None:
                errors.append(f"{name}: canonical 指向不存在的命令 {canonical}")
            elif target.get("status") != status:
                errors.append(f"{name}: 与 canonical {canonical} 状态不一致")
            elif aliases and target.get("aliases") and aliases != target.get("aliases"):
                # 允许别名命令只列部分别名，但必须至少有一个与 canonical 相同。
                if not set(aliases) & set(target["aliases"]):
                    errors.append(f"{name}: 中文别名与 canonical {canonical} 没有交集")
        for field in ("summary", "params", "evidence"):
            text = str(entry.get(field, ""))
            if ANSI_RE.search(text) or LPC_TOKEN_RE.search(text):
                errors.append(f"{name}: {field} 含 ANSI/LPC 残留")

    expected_topics = [r["id"] for r in index]
    if len(expected_topics) != 32:
        errors.append(f"冻结主题口径应为 32 条，实际 {len(expected_topics)} 条")
    topic_seen: dict[str, dict[str, Any]] = {}
    for topic in topics:
        topic_id = topic.get("id")
        if not topic_id:
            errors.append(f"主题缺少 id：{topic!r}")
            continue
        if topic_id in topic_seen:
            errors.append(f"主题重复：{topic_id}")
        topic_seen[topic_id] = topic
        if topic.get("status") not in TOPIC_STATUSES:
            errors.append(f"{topic_id}: status 不在 {TOPIC_STATUSES}")
        if topic.get("category") not in TOPIC_CATEGORIES:
            errors.append(f"{topic_id}: category 不在 {TOPIC_CATEGORIES}")
        body = topic.get("body")
        if not isinstance(body, str) or len(body.strip()) < 40 or not CJK_RE.search(body):
            errors.append(f"{topic_id}: 正文过短或缺少中文")
        elif ANSI_RE.search(body) or LPC_TOKEN_RE.search(body) or "TODO" in body or "TBD" in body:
            errors.append(f"{topic_id}: 正文含 ANSI/LPC/TODO 残留")
        title = topic.get("title")
        if not isinstance(title, str) or not title.strip() or not CJK_RE.search(title):
            errors.append(f"{topic_id}: title 不合法")
    if [t.get("id") for t in topics] != expected_topics:
        errors.append("主题顺序或集合与冻结索引不一致")
    if set(topic_seen) != set(expected_topics):
        errors.append(f"主题集合不一致：缺失={sorted(set(expected_topics) - set(topic_seen))}")

    if errors:
        raise ValueError("校验失败：\n- " + "\n- ".join(errors))


def build_payloads(
    commands: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    inventory: list[dict[str, str]],
    index: list[dict[str, str]],
    frozen_source: dict[str, Any],
    commit: str,
) -> tuple[str, str, str, dict[str, Any]]:
    order = {(r["layer"], r["name"]): i for i, r in enumerate(inventory)}
    source = {
        "repo": frozen_source["repo"],
        "commit": commit,
        "inventory": frozen_source["inventory"],
        "topics_index": frozen_source["topics_index"],
        "note": frozen_source["note"],
    }
    header = (
        "# 由 tools/xkx_help/build.py 生成；请勿手改。\n"
        f"# 源：{source['repo']}@{commit}\n"
        "# 中文正文为人工重写；原版帮助主题只有标题索引，没有逐主题正文文件。"
    )

    commands_sorted = sorted(commands, key=lambda c: order[(c["layer"], c["name"])])
    commands_payload = {"schema_version": 1, "source": source, "commands": commands_sorted}
    commands_yaml = dump_yaml(commands_payload, header)

    titles = {r["id"]: r["title"] for r in index}
    topics_out = []
    for topic in topics:
        item = {
            "id": topic["id"],
            "title": titles.get(topic["id"], topic["title"]),
            "category": topic["category"],
            "status": topic["status"],
        }
        item["body"] = LiteralStr(topic["body"].strip("\n") + "\n")
        topics_out.append(item)
    topics_payload = {"schema_version": 1, "source": source, "topics": topics_out}
    topics_yaml = dump_yaml(topics_payload, header)

    spec = render_spec(commands_sorted, topics_out, source, len(inventory))
    counts = {
        "commands": len(commands_sorted),
        "topics": len(topics_out),
        "status": {s: sum(1 for c in commands_sorted if c["status"] == s) for s in STATUSES},
        "system": {
            s: {
                "已实现": sum(1 for c in commands_sorted if c["system"] == s and c["status"] == "已实现"),
                "待实现": sum(1 for c in commands_sorted if c["system"] == s and c["status"] == "待实现"),
                "不做": sum(1 for c in commands_sorted if c["system"] == s and c["status"] == "不做"),
            }
            for s in SYSTEMS
        },
    }
    return commands_yaml, topics_yaml, spec, counts


def md_escape(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", "<br>")


def render_spec(
    commands: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    source: dict[str, Any],
    expected_count: int,
) -> str:
    status_counts = {s: sum(1 for c in commands if c["status"] == s) for s in STATUSES}
    lines: list[str] = []
    lines.append("# xkx 帮助与命令覆盖矩阵")
    lines.append("")
    lines.append("> 由 `tools/xkx_help/build.py` 生成，请勿手改。")
    lines.append(
        f"> 源：`{source['repo']}@{source['commit']}`；命令口径 {expected_count} 条，"
        f"主题 {len(topics)} 个。"
    )
    lines.append("")
    lines.append("## 一、口径")
    lines.append("")
    lines.append("- **已实现**：玩家当前可以通过引擎动作或只读视图获得该命令的核心效果；局部缺失写在依据里。")
    lines.append("- **待实现**：效果可以映射到现有模型，但命令入口或必要字段尚未接入。")
    lines.append("- **不做**：当前架构不建模，或产品明确排除；不进入排期。")
    lines.append("- 中文别名是复刻版文字壳使用的命令名；xkx 原版使用英文动词，别名列为人工对照，不是源码直译。")
    lines.append("- 原版帮助主题在源仓库中只有 32 条标题索引，没有逐主题正文文件；主题正文按标题与命令源码重写。")
    lines.append("")
    lines.append("## 二、总览")
    lines.append("")
    lines.append("| 状态 | 数量 | 占比 |")
    lines.append("| --- | ---: | ---: |")
    total = len(commands)
    for status in STATUSES:
        count = status_counts[status]
        lines.append(f"| {status} | {count} | {count / total:.1%} |")
    lines.append(f"| 合计 | {total} | 100.0% |")
    lines.append("")
    lines.append("| 归属系统 | 已实现 | 待实现 | 不做 | 合计 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for system in SYSTEMS:
        s = sum(1 for c in commands if c["system"] == system)
        a = sum(1 for c in commands if c["system"] == system and c["status"] == "已实现")
        b = sum(1 for c in commands if c["system"] == system and c["status"] == "待实现")
        d = sum(1 for c in commands if c["system"] == system and c["status"] == "不做")
        lines.append(f"| {system} | {a} | {b} | {d} | {s} |")
    lines.append("")
    lines.append("## 三、逐条覆盖矩阵")
    lines.append("")
    lines.append("| # | 命令 | 中文别名 | 参数 | 一句话说明 | 归属系统 | 状态 | 依据 |")
    lines.append("| ---: | --- | --- | --- | --- | --- | --- | --- |")
    for i, command in enumerate(commands, 1):
        alias = "、".join(command["aliases"])
        if command.get("canonical"):
            alias = f"{alias}（同 {command['canonical']}）"
        lines.append(
            "| {i} | `{name}` | {alias} | {params} | {summary} | {system} | **{status}** | {evidence} |".format(
                i=i,
                name=command["name"],
                alias=md_escape(alias),
                params=md_escape(command["params"]),
                summary=md_escape(command["summary"]),
                system=command["system"],
                status=command["status"],
                evidence=md_escape(command["evidence"]),
            )
        )
    lines.append("")
    lines.append("## 四、引擎现在根本做不到的命令（取舍清单）")
    lines.append("")
    lines.append(f"### 4.1 决定不做（{status_counts['不做']} 条）")
    lines.append("")
    not_do = [c for c in commands if c["status"] == "不做"]
    for group in sorted({c.get("blocker", "其他") for c in not_do}):
        items = [c for c in not_do if c.get("blocker", "其他") == group]
        lines.append(f"**{group}（{len(items)} 条）**")
        lines.append("")
        for c in items:
            lines.append(
                f"- `{c['name']}`（{'、'.join(c['aliases'])}）：{c['evidence']}"
            )
        lines.append("")
    lines.append(f"### 4.2 现在做不到、但可立项（{status_counts['待实现']} 条）")
    lines.append("")
    pending = [c for c in commands if c["status"] == "待实现"]
    for group in sorted({c.get("blocker", "其他") for c in pending}):
        items = [c for c in pending if c.get("blocker", "其他") == group]
        lines.append(f"**{group}（{len(items)} 条）**")
        lines.append("")
        for c in items:
            lines.append(
                f"- `{c['name']}`（{'、'.join(c['aliases'])}）：{c['evidence']}"
            )
        lines.append("")
    lines.append("### 4.3 排期建议")
    lines.append("")
    group_counts = {
        group: sum(1 for c in pending if c.get("blocker", "其他") == group)
        for group in {c.get("blocker", "其他") for c in pending}
    }
    p0 = group_counts.get("战斗系统", 0)
    p1 = group_counts.get("武学/资源系统", 0)
    p2 = sum(
        group_counts.get(g, 0)
        for g in ("师承系统", "社交系统", "任务系统", "战利品系统")
    )
    p3 = sum(
        group_counts.get(g, 0)
        for g in ("玩家档案/文字壳", "系统动作未注册")
    )
    lines.append(f"1. **P0 战斗系统**：补 kill/hit/偷袭等动词与战斗内资源，覆盖 {p0} 条命令。")
    lines.append(f"2. **P1 武学与资源**：补技能种类、激发/准备槽、精/气/内力与招式位，覆盖 {p1} 条武学命令。")
    lines.append(f"3. **P2 师承、社交、任务与战利品**：开放拜师/授课，补打听话题、赠送、结拜、任务定位与拾取，覆盖 {p2} 条。")
    lines.append(f"4. **P3 玩家档案与文字壳**：补别名、清屏、称号、描述、改密、设置与休息注册，覆盖 {p3} 条。")
    lines.append(f"5. **不排期**：4.1 的 {status_counts['不做']} 条按产品取舍关闭；若要重启，必须单独立项并先改架构。")
    lines.append("")
    lines.append("## 五、已实现命令的引擎落点")
    lines.append("")
    implemented = [c for c in commands if c["status"] == "已实现"]
    lines.append(f"共 {len(implemented)} 条。逐条落点见上表「依据」列；均为 `src/`、`GET /api/` 或 `content/` 中可核对的动作/视图。")
    lines.append("")
    for c in implemented:
        lines.append(f"- `{c['name']}`：{c['evidence']}")
    lines.append("")
    lines.append("## 六、复跑与校验")
    lines.append("")
    lines.append("LPC 源码不入库，clone 到仓库外（例如 `/tmp/xkx-research/fluffos-xkx100`）后执行：")
    lines.append("")
    lines.append("```bash")
    lines.append("python3 tools/xkx_help/build.py --src /tmp/xkx-research/fluffos-xkx100")
    lines.append("python3 tools/xkx_help/build.py --verify")
    lines.append("python3 -m pytest tests/test_xkx_help.py -q")
    lines.append("```")
    lines.append("")
    lines.append("`inventory.txt` 与 `topics_index.txt` 是冻结口径；源码条数或主题索引漂移时，`--src` 会直接报错，不会静默改写矩阵。")
    lines.append("输出不含时间戳，重复运行应逐字节一致。")
    lines.append("")
    lines.append("## 七、帮助接入字段契约（玩家壳照此接线）")
    lines.append("")
    lines.append("本内容包只读消费，玩家壳不得因接线修改 `ui/**` 或 `src/wsim/api/**`；壳侧只读取 `content/xkx_help/` 并按下列字段渲染。")
    lines.append("")
    lines.append("### 7.1 主题对象")
    lines.append("")
    lines.append("来源 `content/xkx_help/topics.yaml`。文件结构：`schema_version`、`source`、`topics[]`。")
    lines.append("")
    lines.append("| 字段 | 类型 | 约束 | 接头用途 |")
    lines.append("| --- | --- | --- | --- |")
    lines.append("| `id` | string | 必填；稳定标识，与 `tools/xkx_help/topics_index.txt` 一致 | 路由键；`help <id>` 直接命中 |")
    lines.append(f"| `title` | string | 必填；界面标题 | 列表与详情页标题 |")
    lines.append(f"| `category` | string | 必填；枚举：{'、'.join(TOPIC_CATEGORIES)} | 侧栏/分页分组 |")
    lines.append(f"| `status` | string | 必填；枚举：{'、'.join(TOPIC_STATUSES)} | 未开放/管理主题置灰或加提示 |")
    lines.append("| `body` | string | 必填；多行中文，YAML `\\|` 块文本，保留换行 | 正文渲染；按行切段，不假设 Markdown |")
    lines.append("")
    lines.append("### 7.2 命令对象")
    lines.append("")
    lines.append("来源 `content/xkx_help/commands.yaml`。文件结构：`schema_version`、`source`、`commands[]`；顺序已按源码层（std/usr/skill）冻结。")
    lines.append("")
    lines.append("| 字段 | 类型 | 约束 | 接头用途 |")
    lines.append("| --- | --- | --- | --- |")
    lines.append("| `name` | string | 必填；英文命令名，包内唯一 | 命令查找键 |")
    lines.append("| `layer` | string | 必填；`std` / `usr` / `skill` | 来源分层，排序用 |")
    lines.append("| `aliases` | string[] | 必填；中文别名数组，可含多个 | 中文输入匹配；按别名建索引 |")
    lines.append(f"| `system` | string | 必填；枚举：{'、'.join(SYSTEMS)} | 命令分类页 |")
    lines.append(f"| `status` | string | 必填；枚举：{'、'.join(STATUSES)} | 控制可用性；非「已实现」不可直接执行 |")
    lines.append("| `blocker` | string | 待实现/不做时必填 | 不可用原因的分组标签 |")
    lines.append("| `evidence` | string | 必填；引擎落点或不做理由，含文件与行号 | 详情页依据；排期核对 |")
    lines.append("| `params` | string | 必填；参数提示串 | 补全与用法提示 |")
    lines.append("| `summary` | string | 必填；一句话中文说明 | 列表副标题 |")
    lines.append("| `canonical` | string | 可选；同义归并指向的 `name` | 多个别名共用同一实现时归并 |")
    lines.append("| `parent` | string | 可选；父命令 `name` | 子命令挂载（如 `vote/chblk`） |")
    lines.append("")
    lines.append("壳侧执行前先读 `status`：`已实现` 走引擎动作；`待实现` / `不做` 只展示帮助与 `blocker`，不得当作可执行命令。")
    lines.append("")
    lines.append("### 7.3 manifest 与缓存")
    lines.append("")
    lines.append("`content/xkx_help/manifest.json` 字段：`schema_version`（当前 1）、`generated_by`、`source`（`repo/commit/inventory/topics_index/note`）、`counts`（`commands/topics/status/system`）、`files`（各文件 sha256）、`content_hash`（`commands.yaml + topics.yaml` 的 sha256）。")
    lines.append("")
    lines.append("壳按 `content_hash` 做缓存与校验：哈希不一致时视为内容包已更新，应重新加载；不得在壳内手改已生成文件。")
    lines.append("")
    lines.append("### 7.4 边界")
    lines.append("")
    lines.append("- 本包只负责内容与字面契约；`help` 命令入口、输入匹配、渲染与缓存都在玩家壳完成。")
    lines.append("- 引擎侧命令动作及其注册仍由 `src/wsim/actions/registry.py` 负责；本包不新增动作、不改 API。")
    lines.append("- 新增或调整命令时先改 `tools/xkx_help/data/*.yaml`，再重跑生成器，避免壳侧手写第二份清单。")
    lines.append("")
    return "\n".join(lines)


def write_outputs(out_dir: Path, spec_path: Path, commands_yaml: str, topics_yaml: str, spec: str, counts: dict[str, Any], source: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    (out_dir / "commands.yaml").write_text(commands_yaml, encoding="utf-8")
    (out_dir / "topics.yaml").write_text(topics_yaml, encoding="utf-8")
    spec_path.write_text(spec, encoding="utf-8")
    files = {
        "commands.yaml": sha256_bytes(commands_yaml.encode("utf-8")),
        "topics.yaml": sha256_bytes(topics_yaml.encode("utf-8")),
        "help-coverage.md": sha256_bytes(spec.encode("utf-8")),
    }
    manifest = {
        "schema_version": 1,
        "generated_by": "tools/xkx_help/build.py",
        "source": source,
        "counts": counts,
        "files": files,
        "content_hash": sha256_bytes((commands_yaml + topics_yaml).encode("utf-8")),
    }
    (out_dir / "manifest.json").write_text(dump_json(manifest), encoding="utf-8")


def build(out_dir: Path, spec_path: Path, src: Path | None) -> None:
    frozen_source = json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
    inventory = read_inventory(INVENTORY)
    index = read_topics_index(TOPICS_INDEX)
    if src is not None:
        assert_same("命令清单", scan_source_inventory(src), inventory)
        assert_same("主题索引", scan_source_topics(src), index)
    commands = load_authored_commands()
    topics = load_authored_topics()
    validate(commands, topics, inventory, index)
    commit = source_commit(src, frozen_source)
    commands_yaml, topics_yaml, spec, counts = build_payloads(
        commands, topics, inventory, index, frozen_source, commit
    )
    source = {
        "repo": frozen_source["repo"],
        "commit": commit,
        "inventory": frozen_source["inventory"],
        "topics_index": frozen_source["topics_index"],
        "note": frozen_source["note"],
    }
    write_outputs(out_dir, spec_path, commands_yaml, topics_yaml, spec, counts, source)


def verify(out_dir: Path, spec_path: Path, src: Path | None) -> int:
    with tempfile.TemporaryDirectory(prefix="xkx-help-") as tmp:
        tmp_dir = Path(tmp) / "content"
        tmp_spec = Path(tmp) / "help-coverage.md"
        build(tmp_dir, tmp_spec, src)
        pairs = [
            (tmp_dir / "commands.yaml", out_dir / "commands.yaml"),
            (tmp_dir / "topics.yaml", out_dir / "topics.yaml"),
            (tmp_dir / "manifest.json", out_dir / "manifest.json"),
            (tmp_spec, spec_path),
        ]
        failed = False
        for generated, committed in pairs:
            if not committed.is_file():
                print(f"缺少产物：{committed}")
                failed = True
                continue
            if generated.read_bytes() != committed.read_bytes():
                print(f"产物已过期：{committed}")
                failed = True
        return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 xkx 帮助内容包与覆盖矩阵")
    parser.add_argument("--src", type=Path, help="xkx100 源码目录（校验 153/32 口径）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="内容包输出目录")
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC, help="覆盖矩阵输出路径")
    parser.add_argument("--verify", action="store_true", help="只校验已提交产物是否与重新生成一致")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            return verify(args.out, args.spec, args.src)
        build(args.out, args.spec, args.src)
        print(f"已生成：{args.out}/commands.yaml, topics.yaml, manifest.json")
        print(f"已生成：{args.spec}")
        return 0
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
