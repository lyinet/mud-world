"""W3b：scaleup_w3 的 29 张人物卡覆盖、字数、去重、自洽与对话命中。"""
from __future__ import annotations

import difflib
import itertools
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from wsim.agent.dialogue import (  # noqa: E402
    extract_persona_profile,
    extract_persona_voice,
    place_names,
)
from wsim.core.content import load_content  # noqa: E402
import content_scale  # noqa: E402

PACK = "scaleup_w3"
PACK_DIR = ROOT / "content" / "packs" / PACK
PERSONA_DIR = PACK_DIR / "personas"


def _enabled_content(monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("XKX_CONTENT_PACKS", PACK)
    return load_content(ROOT, allow_fixture=False)


def _pack_npcs():
    data = yaml.safe_load((PACK_DIR / "npcs.yaml").read_text(encoding="utf-8"))
    return {n["id"]: n for n in data["npcs"]}


def _sentences(text: str):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        for sent in re.split(r"[。！？；]+", line):
            sent = sent.strip().strip("“”")
            if len(sent) >= 6:
                out.append(sent)
    return out


def test_cards_cover_all_new_npcs(monkeypatch):
    content = _enabled_content(monkeypatch)
    npc_ids = set(_pack_npcs())
    cards = {p.stem for p in PERSONA_DIR.glob("*.md")}
    assert cards == npc_ids
    assert all(content.personas.get(nid) for nid in npc_ids)
    assert len(content.personas) == 59


def test_card_length_is_200_to_400_han_chars():
    for path in sorted(PERSONA_DIR.glob("*.md")):
        han = len(re.findall(r"[\u4e00-\u9fff]", path.read_text(encoding="utf-8")))
        assert 200 <= han <= 400, f"{path.name}: {han} 汉字"


def test_every_card_yields_voice_and_unique_catchphrase(monkeypatch):
    content = _enabled_content(monkeypatch)
    quotes = []
    for nid in sorted(_pack_npcs()):
        voice = extract_persona_voice(content.personas[nid])
        assert voice["style"], f"{nid} 缺少说话风格"
        assert voice["reaction"], f"{nid} 缺少遇事反应"
        assert len(voice["quotes"]) >= 2, f"{nid} 台词不足"
        # 引擎会丢弃含第三人称他/她的台词，这里提前拦住。
        assert all("他" not in q and "她" not in q for q in voice["quotes"]), nid
        quotes.extend(voice["quotes"])
    assert len(quotes) == len(set(quotes)), "存在跨卡重复台词"


def test_any_five_cards_sentence_similarity_below_half(monkeypatch):
    content = _enabled_content(monkeypatch)
    ids = sorted(_pack_npcs())
    worst = 0.0
    for a, b in itertools.combinations(ids, 2):
        score = max(
            (
                difflib.SequenceMatcher(None, x, y).ratio()
                for x in _sentences(content.personas[a])
                for y in _sentences(content.personas[b])
            ),
            default=0.0,
        )
        worst = max(worst, score)
    # 全量两两检查比“任意抽 5 张”更严格。
    assert worst < 0.5, f"最高句级相似度 {worst:.3f}"


def test_cards_agree_with_npcs_yaml(monkeypatch):
    content = _enabled_content(monkeypatch)
    names = {n["id"]: n["name"] for n in content.npcs}
    for nid, npc in _pack_npcs().items():
        text = content.personas[nid]
        lines = text.splitlines()
        home = next(l for l in lines if l.startswith("- 居住地："))
        identity = next(l for l in lines if l.startswith("- 身份："))
        assert home.split("：", 1)[1].strip() == npc["home"], nid
        assert npc["role"] in identity, nid
        tension = text.split("## 与谁有张力", 1)[1].split("##", 1)[0]
        related = [names.get(r["target"], r["target"]) for r in npc.get("relations", [])]
        assert sum(1 for name in related if name in tension) >= 2, nid


def test_dialogue_fallback_rate_is_zero():
    result = content_scale.dialogue_probe(PACK, days=2)
    assert result["new_npcs"] == 29
    assert result["cards_present"] == 29
    assert result["cards_missing"] == []
    assert result["fallback_rate"] == 0.0
    assert result["place_mismatches"] == []
    assert all(row["fallbacks"] == 0 for row in result["per_npc"].values())


def test_open_mode_places_follow_loaded_locations(monkeypatch):
    """地点名取自 load_content.locations，新地点不得回退成“集市”。"""
    content = _enabled_content(monkeypatch)
    names = place_names(content)
    assert names["charcoal_kiln"] == "炭窑"
    assert names["dye_works"] == "染坊"
    seen: set[str] = set()
    for npc in _pack_npcs().values():
        profile = extract_persona_profile(content.personas[npc["id"]], npc["role"], names)
        seen.add(profile["place"])
        assert profile["place"] == names[npc["home"]], npc["id"]
        # 要求 3：不存在 place=集市 但 home 不是集市的 NPC。
        assert not (profile["place"] == "集市" and npc["home"] != "market"), npc["id"]
    assert {"炭窑", "染坊"}.issubset(seen)


def test_materialize_can_include_pack_personas(tmp_path):
    """开启态物化覆盖 59 张卡；函数默认仍保留 W3 旧基线 30 张。"""
    out = tmp_path / "content"
    counts = content_scale.materialize(PACK, out, include_pack_personas=True)
    assert counts["personas"] == 59
    enabled = load_content(tmp_path, allow_fixture=False)
    assert len(enabled.personas) == 59
    assert set(_pack_npcs()) <= set(enabled.personas)
