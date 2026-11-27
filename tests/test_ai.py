"""AI 服務與技術分析模組的離線測試（不呼叫任何 AI 服務、不連網）。

技術分析部分使用程式產生的測試價格（不是任何股票的真實行情），用來驗證公式與規則。
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from core.ai import llm
from core.ai.facts import Fact, FactSheet
from core.ai.guard import check_numbers
from core.ai.tutor import explain, review
from core.analysis.technical import compute, kd
from core.instruments import load_master
from core.normalize import finalize
from core.providers.base import Bars, DataKind
from core.quality import check

TSMC = load_master()["TW"]["2330"]
AAPL = load_master()["US"]["AAPL"]
NOW = datetime(2026, 11, 27, 7, 0, tzinfo=timezone.utc)


def sheet() -> FactSheet:
    fs = FactSheet("2330 台積電")
    fs.add(Fact("tech.rsi.rsi14", "RSI(14)", 42.82, "", "2026-11-26", "test", decimals=1))
    fs.add(Fact("tech.ma.sma20", "20 日均線", 2222.5, "TWD", "2026-11-26", "test"))
    fs.add(Fact("tech.vol.last", "最新成交量", 45892.693, "張", "2026-11-26", "test", decimals=0))
    fs.add(Fact("tech.levels.swing_high_last", "波段高點", 2590.0, "TWD", "2026-11-26", "test"))
    fs.add(Fact("tech.ma.last_cross", "最近交叉", "2026-10-16 死亡交叉", "", "2026-11-26", "test"))
    fs.add(Fact("fund.pe", "本益比", float("nan")))
    return fs


# ---- 數字查核 -------------------------------------------------------------------
@pytest.mark.parametrize("text", ["RSI(14) 是 42.8", "RSI 約 43", "月線 2,222.50", "月線約 2,222", "成交量 45,893 張",
                                  "約 4.6 萬張", "11/26 的資料", "2026-10-16 出現死亡交叉", "14 日 RSI 低於 70", "第 3 步"])
def test_guard_accepts_grounded_numbers(text):
    assert check_numbers(text, sheet()).ok, check_numbers(text, sheet()).unsupported


@pytest.mark.parametrize("text,bad", [("RSI 是 55.3", "55.3"), ("可能漲到 2,600 元", "2,600"), ("月線 2,230", "2,230"),
                                      ("2026-12-31 公布財報", "2026-12-31"), ("11/30 收盤", "11/30"),
                                      ("成交量 99,999 張", "99,999")])
def test_guard_rejects_invented_numbers_and_dates(text, bad):
    r = check_numbers(text, sheet())
    assert not r.ok and bad in r.unsupported


def test_missing_values_are_listed_not_numbers():
    fs = sheet()
    assert "本益比" in fs.missing and "fund.pe" not in fs.facts
    assert '"unavailable": [\n  "本益比"' in fs.to_prompt()


# ---- AI 家教審查流程 ----------------------------------------------------------------
class FakeBackend(llm.LLMBackend):
    name, model = "Fake", "test"

    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def generate_json(self, system, prompt, schema):
        self.prompts.append((system, prompt))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


GOOD = {"beginner_explanation": "RSI(14) 目前是 42.8，介於 30 和 70 之間，屬於中性區。",
        "possibilities": [{"scenario": "若 RSI 回到 50 以上", "what_to_watch": "觀察是否站回月線 2,222.50"},
                          {"scenario": "若 RSI 跌破 30", "what_to_watch": "觀察成交量是否放大"}],
        "uncertain": ["本益比目前無法取得"], "facts_used": ["tech.rsi.rsi14", "tech.ma.sma20"],
        "question_for_user": "你覺得 RSI 和均線的訊號一致嗎？"}


@pytest.fixture(autouse=True)
def reset_backend():
    yield
    llm.set_backend(None)


def test_tutor_accepts_grounded_answer_and_sends_only_facts():
    fake = FakeBackend(GOOD)
    llm.set_backend(fake)
    ans = explain("RSI", sheet(), "RSI(14) = 42.8")
    assert ans.status == "ok" and ans.facts_used == ["tech.rsi.rsi14", "tech.ma.sma20"]
    system, prompt = fake.prompts[0]
    assert "只能使用" in system and "不可預測未來股價" in system
    assert "tech.rsi.rsi14" in prompt and "本益比" in prompt       # 缺漏項目也告訴 AI


@pytest.mark.parametrize("mutate,reason", [
    (lambda d: d.update(beginner_explanation="RSI 是 55.3"), "沒有資料依據的數字"),
    (lambda d: d.update(facts_used=["tech.made.up"]), "不存在的事實"),
    (lambda d: d.update(question_for_user="建議買進嗎？"), "買賣建議"),
    (lambda d: d.update(beginner_explanation="這檔股票保證會漲"), "保證"),
    (lambda d: d["possibilities"].append({"scenario": "目標價 2,590", "what_to_watch": "x"}), "買賣建議或保證用語"),
])
def test_tutor_rejects_ungrounded_or_advice(mutate, reason):
    bad = {**GOOD, "possibilities": list(GOOD["possibilities"])}
    mutate(bad)
    ok, why = review(bad, sheet())
    assert not ok and reason in why


def test_tutor_without_key_is_unavailable_not_invented():
    llm.set_backend(llm.DisabledBackend("伺服器尚未設定 GEMINI_API_KEY"))
    ans = explain("RSI", sheet())
    assert ans.status == "unavailable" and "GEMINI_API_KEY" in ans.reason and not ans.explanation


def test_tutor_service_failure_is_unavailable():
    llm.set_backend(FakeBackend(llm.LLMUnavailable("Gemini 服務暫時無法使用（Timeout）")))
    assert explain("RSI", sheet()).status == "unavailable"


# ---- 技術分析模組 --------------------------------------------------------------------
def synthetic_bars(inst, n=400, last_kind=DataKind.HISTORICAL):
    """測試用價格：上升趨勢加上週期波動（不是任何股票的真實資料）。"""
    idx = pd.bdate_range("2025-03-03", periods=n)
    t = np.arange(n)
    close = 100 + t * 0.15 + 8 * np.sin(t / 9)
    df = pd.DataFrame({"open": close - 0.5 * np.cos(t), "close": close, "volume": 1_000_000 + 200_000 * np.sin(t / 5)},
                      index=idx.tz_localize(inst.timezone))
    df["high"] = df[["open", "close"]].max(axis=1) + 1
    df["low"] = df[["open", "close"]].min(axis=1) - 1
    df["turnover"] = np.nan
    frame = finalize(inst, df, NOW)
    if last_kind == DataKind.DELAYED:
        frame.iloc[-1, frame.columns.get_loc("kind")] = DataKind.DELAYED.value
    return Bars(inst, frame, frame.copy(), "test", NOW)


def test_kd_matches_taiwan_formula():
    df = pd.DataFrame({"High": [10, 11, 12, 13, 14, 15, 16, 17, 18, 19.0], "Low": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18.0],
                       "Close": [9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5, 18.5]})
    k, d = kd(df, 9)
    rsv8 = (17.5 - 9) / (18 - 9) * 100   # 第 9 天（index 8）：收盤 17.5，9 日最低 9、最高 18
    k8 = 50 * 2 / 3 + rsv8 / 3
    assert k.iloc[8] == pytest.approx(k8) and d.iloc[8] == pytest.approx(50 * 2 / 3 + k8 / 3)
    assert np.isnan(k.iloc[7])


@pytest.mark.parametrize("inst", [TSMC, AAPL])
def test_technical_report_is_complete_and_self_consistent(inst):
    bars, rep = check(synthetic_bars(inst), NOW)
    r = compute(inst, bars, rep)
    assert [s.key for s in r.sections] == ["candle", "ma", "macd", "rsi", "kd", "bb", "volume", "levels"]
    assert list(r.timeframes["週期"]) == ["日線", "週線", "月線"]
    assert len(r.cases) == 4 and all(c.n >= 0 for c in r.cases)
    for s in r.sections:
        g = check_numbers(" ".join(s.reading), r.facts)
        assert g.ok, (s.title, g.unsupported)            # 規則式解讀的每個數字都要能追溯到事實
        assert s.principle and s.possibilities
        if s.quiz:
            assert s.quiz.answer in s.quiz.options
    assert r.facts.get("tech.rsi.rsi14") is not None
    assert all(f.source.startswith("test") for f in r.facts.facts.values())
    unit = "張" if inst.market == "TW" else "股"
    assert r.facts.get("tech.vol.last").unit == unit


def test_delayed_bar_is_flagged_in_readings():
    bars, rep = check(synthetic_bars(TSMC, last_kind=DataKind.DELAYED), NOW)
    r = compute(TSMC, bars, rep)
    candle = next(s for s in r.sections if s.key == "candle")
    assert r.kind == DataKind.DELAYED and any("盤中" in line for line in candle.reading)
    assert all(f.kind == DataKind.DELAYED.value for f in candle.facts.facts.values() if f.id.startswith("tech.candle.close"))
