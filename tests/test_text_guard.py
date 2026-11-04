"""模型输出层硬伤修复：格式 sanitizer、人称改写、口号去重。

fixture 全部来自真实盲评证据（不调用任何模型接口）：
- `reports/evidence/blind-eval-r3/model-replies.jsonl`（40 条真实回复）
- `reports/evidence/blind-eval-r3/pairs.jsonl` + `answer_key.jsonl`（70 对盲评原文）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from wsim.agent.prompt import (
    DECISION_SYSTEM_PROMPT,
    DIALOGUE_SYSTEM_PROMPT,
    REPLY_HARD_CONSTRAINTS,
    SYSTEM_PROMPT,
)
from wsim.agent.text_guard import (
    DEFAULT_PHRASE_WINDOW,
    PhraseLedger,
    sanitize_format,
    sanitize_reply,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "reports/evidence/blind-eval-r3"
NPC = "段五斗"
# result-r4.md 点名的 7 条模型失分对。
LOSING_IDS = (2, 10, 13, 15, 19, 20, 23)
# result-r4.md 点名的格式/人称问题对。
FORMAT_IDS = (4, 35, 2, 66)
PERSON_IDS = (15, 52, 60)

_BAD_MARKERS = re.compile(r"\*|【|】")
_PAREN_ACTION = re.compile(r"[（(][^）)]{1,40}[）)]")


def _load_replies() -> dict[int, dict]:
    rows = [json.loads(line) for line in EVIDENCE.joinpath("model-replies.jsonl").read_text().splitlines()]
    return {int(row["id"]): row for row in rows}


def _load_pairs() -> dict[int, dict]:
    pairs = {int(row["id"]): row for row in map(json.loads, EVIDENCE.joinpath("pairs.jsonl").read_text().splitlines())}
    key = {int(row["id"]): row["model"] for row in map(json.loads, EVIDENCE.joinpath("answer_key.jsonl").read_text().splitlines())}
    return {pid: {**pairs[pid], "model_text": pairs[pid][key[pid]]} for pid in pairs}


REPLIES = _load_replies()
PAIRS = _load_pairs()


def _sanitize(text: str, ledger: PhraseLedger | None = None):
    return sanitize_reply(text, npc=NPC, npc_names=(NPC,), ledger=ledger)


@pytest.mark.parametrize("prompt_name", ["system", "decision", "dialogue"])
def test_prompt_contains_hard_constraints(prompt_name: str):
    text = {
        "system": SYSTEM_PROMPT,
        "decision": DECISION_SYSTEM_PROMPT,
        "dialogue": DIALOGUE_SYSTEM_PROMPT,
    }[prompt_name]
    assert "第一人称" in text
    assert "第三人称" in text
    assert "Markdown" in text
    assert "口头禅" in text or "口号" in text
    if prompt_name == "dialogue":
        assert REPLY_HARD_CONSTRAINTS in text


@pytest.mark.parametrize("rid", FORMAT_IDS)
def test_markdown_and_halfwidth_action_are_fixed(rid: int):
    raw = PAIRS[rid]["model_text"]
    out, flags, unresolved = sanitize_format(raw, npc_names=(NPC,))
    assert not _BAD_MARKERS.search(out), out
    assert not _PAREN_ACTION.search(out), out
    assert not unresolved
    assert "action_mark" in flags or "markdown_action" in flags


def test_normal_replies_are_not_rewritten():
    """不含格式/人称问题的正常回复必须逐字保留。"""
    for rid in (3, 10, 21, 24, 37, 40):
        out, flags, unresolved = sanitize_format(REPLIES[rid]["reply"], npc_names=(NPC,))
        assert out == REPLIES[rid]["reply"]
        assert not flags
        assert not unresolved


def test_third_person_pronouns_for_other_people_are_kept():
    """id1/id3/id21 里的“她/他”指别人，不能当第三人称叙述误伤。"""
    for rid in (1, 3, 21):
        _out, flags, _unresolved = sanitize_format(REPLIES[rid]["reply"], npc_names=(NPC,))
        assert "third_person" not in flags


@pytest.mark.parametrize("pid", PERSON_IDS)
def test_named_third_person_is_rewritten_to_first_person(pid: int):
    raw = PAIRS[pid]["model_text"]
    out, flags, unresolved = sanitize_format(raw, npc_names=(NPC,))
    assert "third_person" in flags
    assert not unresolved
    assert not out.startswith(NPC)
    assert "他自己" not in out
    assert out.startswith("“") or out.startswith("我")
    assert not _PAREN_ACTION.search(out)
    assert not _BAD_MARKERS.search(out)


def test_seven_losing_replies_fix_format_and_slogan_in_batch():
    """按 7 条失分原文的顺序跑同一跑批台账；口号第二次起必须改写。"""
    ledger = PhraseLedger(window=DEFAULT_PHRASE_WINDOW)
    results = {}
    for pid in LOSING_IDS:
        results[pid] = _sanitize(PAIRS[pid]["model_text"], ledger)

    for pid in (2, 13, 15, 23):
        assert not _BAD_MARKERS.search(results[pid].text)
        assert not _PAREN_ACTION.search(results[pid].text)
    # id15：第三人称小说叙述已改成直接引语。
    assert "third_person" in results[15].flags
    assert results[15].text.lstrip("“").startswith("就我一个光棍汉")
    # id13 是该窗口内第一次“包在我身上”，允许一次；id20/id23 必须换说法。
    assert "包在我身上" in results[13].text
    assert results[20].repeated_phrase == "包在我身上"
    assert "包在我身上" not in results[20].text
    assert "包在我身上" not in results[23].text
    # id10/id19 没有格式/人称硬伤，保持原样。
    assert results[10].text == PAIRS[10]["model_text"]
    assert results[19].text == PAIRS[19]["model_text"]


def test_slogan_second_use_within_window_is_rewritten():
    ledger = PhraseLedger(window=3)
    first = sanitize_reply("包在我身上，我这就去办。", npc=NPC, npc_names=(NPC,), ledger=ledger)
    assert "包在我身上" in first.text
    second = sanitize_reply("这事包在我身上！", npc=NPC, npc_names=(NPC,), ledger=ledger)
    assert "包在我身上" not in second.text
    assert second.repeated_phrase == "包在我身上"
    assert "phrase_repeated" in second.flags
    assert second.text == "这事我应下了！"


def test_slogan_name_variants_share_one_window():
    ledger = PhraseLedger(window=3)
    first = sanitize_reply("包在我段五斗身上！", npc=NPC, npc_names=(NPC,), ledger=ledger)
    assert "包在我段五斗身上" in first.text
    second = sanitize_reply("包在我身上！", npc=NPC, npc_names=(NPC,), ledger=ledger)
    assert "包在我身上" not in second.text
    assert second.repeated_phrase == "包在我身上"


def test_slogan_window_expires_and_npcs_are_independent():
    ledger = PhraseLedger(window=2)
    assert "包在我身上" in _sanitize("包在我身上。", ledger).text
    _sanitize("我在摊上。", ledger)
    _sanitize("今天肉新鲜。", ledger)
    # 已滑出最近 2 条窗口，再次使用允许。
    assert "包在我身上" in _sanitize("包在我身上。", ledger).text
    # 另一个 NPC 不共享窗口。
    other = sanitize_reply("包在我身上。", npc="宋鹤年", npc_names=("宋鹤年",), ledger=ledger)
    assert "包在我身上" in other.text


def test_ledger_roundtrip_is_deterministic():
    ledger = PhraseLedger(window=2)
    sanitize_reply("包在我身上。", npc=NPC, npc_names=(NPC,), ledger=ledger)
    restored = PhraseLedger.from_dict(ledger.to_dict())
    same = sanitize_reply("包在我身上。", npc=NPC, npc_names=(NPC,), ledger=restored)
    assert "包在我身上" not in same.text
    assert same.text == sanitize_reply(
        "包在我身上。", npc=NPC, npc_names=(NPC,), ledger=PhraseLedger.from_dict(ledger.to_dict())
    ).text


def test_self_narration_is_rewritten_and_fallback_not_needed():
    raw = "他自己默默走到门口，没有再回头。"
    out, flags, unresolved = sanitize_format(raw, npc_names=(NPC,))
    assert "third_person" in flags and not unresolved
    assert out.startswith("我默默走到门口")
    result = sanitize_reply(raw, npc=NPC, npc_names=(NPC,), fallback="我这就走，不回头了。")
    assert result.text == out
    assert "fallback_template" not in result.flags


def test_all_40_replies_have_no_banned_markup():
    for row in REPLIES.values():
        out, _flags, unresolved = sanitize_format(row["reply"], npc_names=(NPC,))
        assert not unresolved
        assert not _BAD_MARKERS.search(out)
        assert not _PAREN_ACTION.search(out)
