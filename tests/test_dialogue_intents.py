"""意图分化回归：不同类别必须用不同句式骨架，不能只换话题名词。"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from wsim.agent.dialogue import compose_talk

CATEGORY_CASES = {
    "price": [
        "今日肉价如何？",
        "这肉多少钱一斤？",
        "价钱能不能让一点？",
        "你这铺子一天卖多少？",
        "今日可有新鲜猪下水？",
    ],
    "credit": [
        "若有人赊账，你怎么办？",
        "手头紧，能赊一回吗？",
        "借钱给你，什么时候还？",
        "赊账的账期怎么算？",
        "欠你的账能缓两天吗？",
    ],
    "directions": [
        "哪里能找到落脚的地方？",
        "去客栈怎么走？",
        "从集市到河渡怎么去？",
        "我迷路了，住处在哪？",
        "附近哪儿能歇脚？",
    ],
    "gossip": [
        "金掌柜为人如何？",
        "你觉得邢捕快办案公道吗？",
        "这镇上谁最有钱？",
        "最近镇上可有什么新鲜事？",
        "你和孟九娘熟吗？",
    ],
    "chitchat": [
        "你平日里除了卖肉还做什么？",
        "你这刀工是跟谁学的？",
        "若是官府加税，你怕不怕？",
        "最后一个问题：你信命吗？",
        "夜里市场可安全？",
    ],
    "help": [
        "若我想学门手艺，你有什么建议？",
        "能帮我抬一下案板吗？",
        "劳烦你帮我看看这刀。",
        "我请你喝碗酒，肯赏脸吗？",
        "能不能借我个火？",
    ],
    "rejection": [
        "我不买了，多谢。",
        "不用了，算了。",
        "免了，我没兴趣。",
        "不要，你走开。",
        "滚，别烦我。",
    ],
    "deflection": [
        "多谢你照应。",
        "今天天气不错。",
        "嗯。",
        "没什么。",
        "随便聊聊。",
    ],
}


def _replies(real_content):
    persona = real_content.personas["duan_wudou"]
    out: dict[str, list[dict]] = {}
    for category, messages in CATEGORY_CASES.items():
        rows = []
        for i, message in enumerate(messages):
            rows.append(
                compose_talk(
                    persona, "duan_wudou", "段五斗", "屠户", message, 100 + i
                )
            )
        out[category] = rows
    return out


def test_at_least_seven_categories(real_content):
    rows = _replies(real_content)
    assert len(rows) >= 7
    for category, items in rows.items():
        assert len(items) == 5
        assert all(item["intent"] == category for item in items), category


def test_categories_use_disjoint_skeletons(real_content):
    rows = _replies(real_content)
    skeletons = {category: {item["skeleton"] for item in items} for category, items in rows.items()}
    categories = list(skeletons)
    for i, left in enumerate(categories):
        for right in categories[i + 1:]:
            assert not skeletons[left] & skeletons[right], (left, right)


def test_cross_category_text_similarity_below_60_percent(real_content):
    rows = _replies(real_content)
    worst = 0.0
    worst_pair = None
    categories = list(rows)
    for i, left in enumerate(categories):
        for right in categories[i + 1:]:
            pair_max = max(
                SequenceMatcher(None, a["reply"], b["reply"]).ratio()
                for a in rows[left]
                for b in rows[right]
            )
            if pair_max > worst:
                worst, worst_pair = pair_max, (left, right)
    assert worst < 0.60, f"跨类别相似度 {worst:.3f} 超阈值：{worst_pair}"


def test_intent_replies_keep_format_contract(real_content):
    rows = _replies(real_content)
    for items in rows.values():
        for item in items:
            reply = item["reply"]
            assert 20 <= len(reply) <= 60, (item["skeleton"], len(reply), reply)
            assert not re.search(r"\*\*|__|`|^#", reply), reply
            assert not re.search(r"(?<!其)他(?!人)|她", reply), reply
            assert "我" in reply, reply
