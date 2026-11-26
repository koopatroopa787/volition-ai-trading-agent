"""Run with pytest, or `python tests/test_factcheck.py`.

Fixtures are sentences the committee actually wrote (decisions.jsonl, Sep-Oct 2026).
Each "must not flag" case is a false positive the checker produced during development.
"""
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.factcheck import (BASE_POLICY, check, facts_from_prompt, grade, policy_verdict, review,
                              supports)


def F(**over):
    base = {"strategy": "bull_call_spread", "trend": 0.97, "iv_rank": 10.0, "rvol_pct": 49.0, "news": -0.02,
            "catalyst": None, "confidence": 0.92, "rr": 1.94, "max_loss": 1000.0, "max_loss_pct": 0.85,
            "budget_pct": 1.25, "p_profit_pct": 50.56, "p_near_pct": 50.24, "exp_pnl": 89.44,
            "policy": BASE_POLICY}
    return {**base, **over}


def kinds(text, facts=None, status="false"):
    return sorted(c.kind for c in check([text], facts or F()) if c.status == status)


# ── must flag ───────────────────────────────────────────────────────────────

def test_false_comparison_between_stated_numbers():
    t = "the probability of near-maximum loss is 50.24%, which exceeds the 60% threshold set by the stress test's pass policy."
    assert kinds(t) == ["compare"]
    assert grade(check([t], F()), strict=True) == "false"


def test_probability_below_threshold_when_it_is_above():
    assert kinds("the stress test indicates a probability of profit of 50.56%, which is below the 35% threshold.") == ["compare"]


def test_ratio_compared_to_budget_percent():
    t = "the reward-to-risk ratio (1.226) exceeds the risk budget (1.25% of equity)."
    assert kinds(t, F(rr=1.226)) == ["budget", "compare"]


def test_other_gates_are_not_the_per_trade_budget():
    """Portfolio and symbol limits can genuinely be exceeded; only the per-trade budget is guaranteed."""
    assert "budget" not in kinds("the portfolio open risk exceeds the 8% limit.")


def test_max_loss_misquoted_by_a_factor_of_ten():
    t = "the proposal is within the risk budget with a maximum loss of 8.93% of equity."
    assert kinds(t, F(max_loss_pct=0.89)) == ["value"]


def test_max_loss_exceeds_budget_asserted_when_inside():
    assert "budget" in kinds("the proposal's risk budget is insufficient, as the maximum loss exceeds the stated equity budget.")


def test_iv_called_high_when_percentile_is_low():
    cs = check(["the proposal's risk budget is insufficient given the high iv rank."], F(iv_rank=2.1))
    assert [c.kind for c in cs if c.status == "false"] == ["iv_level"]
    assert grade(cs, strict=True) == "unchecked"          # soft findings never count toward enforcement
    assert grade(cs) == "false"


def test_invented_threshold():
    cs = check(["the reward-to-risk ratio is above the typical threshold of 1.5, which is a warning sign."], F())
    assert [c.status for c in cs if c.kind == "invented"] == ["unsupported"]


def test_misstated_policy_threshold():
    assert "invented" in kinds("probability of profit greater than 35%, which exceeds the required threshold of 41.56%.",
                               status="unsupported")


def test_misalignment_claim_contradicts_a_passing_role_check():
    t = "the proposed bear put spread strategy is not aligned with the current bearish volatility expansion regime."
    cs = check([t], F(strategy="bear_put_spread", trend=-0.72))
    assert [c.kind for c in cs if c.status == "false"] == ["alignment"]
    assert grade(cs, strict=True) == "unchecked"          # soft: reported, never enforced
    # the same sentence is a fair statement when the role check really fails
    assert not [c for c in check([t], F(strategy="bear_put_spread", trend=-0.1)) if c.kind == "alignment"]
    # and a misalignment with something other than the regime is not this finding
    assert not [c for c in check(["the structure is not aligned with the stated risk budget."], F()) if c.kind == "alignment"]


# ── must not flag (each was a false positive once) ───────────────────────────

def test_true_comparison_is_ok():
    assert grade(check(["the trend score is +0.97, which is well above the required +0.35."], F())) == "ok"


def test_number_must_sit_beside_the_relation():
    t = "the risk is 1.25% equity and the stress test indicates a probability of profit greater than 35%."
    assert not [c for c in check([t], F()) if c.kind == "compare"]


def test_threshold_mention_is_not_a_value_claim():
    t = "the trend score of +0.72 is above the minimum required trend score of +0.35 for the bull call spread."
    assert grade(check([t], F(trend=0.7195))) == "ok"
    t = "the trend score of -0.9892 is far more negative than the bearish regime's trend score of -0.35."
    assert grade(check([t], F(trend=-0.9892))) == "ok"


def test_rounding_to_written_precision_is_not_an_error():
    assert grade(check(["the high iv rank (74th percentile) fits."], F(iv_rank=74.74, strategy="iron_condor"))) == "ok"
    assert grade(check(["the positive news sentiment of 0.1 supports it."], F(news=0.12))) == "ok"


def test_ranges_and_hedges_are_not_assertions():
    assert not check(["the risk budget of 1.25% exceeds the typical risk budget of 1% to 2% of equity."], F(), ) or \
        grade(check(["a typical risk budget of 1% to 2% of equity."], F())) != "false"
    assert "budget" not in kinds("a loss of this size could exceed the stated equity budget if the thesis fails.")
    assert "budget" not in kinds("the proposal's risk parameters meet or exceed the established risk budget.")


def test_negative_numbers_loose_wording_skipped():
    assert not kinds("however, the trend score is -0.58, which is above the threshold of -0.35 required for the bear put spread.",
                     F(trend=-0.58, strategy="bear_put_spread"))


def test_adjective_must_modify_iv_itself():
    assert not [c for c in check(["the iv rank and high realized volatility indicate uncertainty."], F(iv_rank=5))
                if c.kind == "iv_level"]
    assert not [c for c in check(["the iv rank is not high."], F(iv_rank=5)) if c.kind == "iv_level"]


def test_true_low_iv_is_ok():
    assert [c.status for c in check(["the low iv supports buying premium."], F(iv_rank=5)) if c.kind == "iv_level"] == ["true"]


def test_policy_numbers_are_not_invented():
    assert not [c for c in check(["trend is above the required threshold of +0.35."], F()) if c.kind == "invented"]


def test_deterministic_fallback_text_is_clean():
    """Control: the fallback writes from true values, so a flag here means the checker is wrong."""
    f = F(trend=0.65, iv_rank=77.84, rvol_pct=28.8, max_loss=1183.5, confidence=0.8304, max_loss_pct=1.17)
    for parts in (["The risk-on trend classification supports the structure.", "trend +0.65", "regime risk-on trend"],
                  ["The premium regime is consistent with the selected defined-risk structure.", "IV score 78", "realized vol 28.8%"],
                  ["The proposal has finite loss and explicit invalidation.", "max loss $1,184", "confidence 83%"]):
        assert grade(check(parts, f)) == "ok", parts


# ── verdicts and wiring ─────────────────────────────────────────────────────

def test_policy_verdict_matches_committee_role_check():
    assert policy_verdict("Regime Sentinel", F(trend=0.5)) is True
    assert policy_verdict("Regime Sentinel", F(trend=0.2)) is False
    assert policy_verdict("Regime Sentinel", F(strategy="bear_put_spread", trend=-0.4)) is True
    assert policy_verdict("Volatility Architect", F()) is True
    assert policy_verdict("Volatility Architect", F(strategy="iron_condor", iv_rank=40)) is False
    assert policy_verdict("Volatility Architect", F(strategy="long_strangle", iv_rank=10)) is None   # needs catalyst
    assert policy_verdict("Adversarial Skeptic", F()) is True
    assert policy_verdict("Adversarial Skeptic", F(max_loss_pct=1.3)) is False
    assert policy_verdict("Adversarial Skeptic", F(confidence=0.6)) is False


def test_supports_matches_the_gate():
    assert supports(["support", "support", "oppose"]) and supports(["support", "support", "abstain"])
    assert not supports(["support", "oppose", "oppose"]) and not supports(["support", "abstain", "abstain"])


def test_facts_from_prompt_reads_the_committee_evidence_shape():
    ev = {"market": {"trend_score_minus1_to1": 0.56, "iv_rank_percentile_0_to100": 52.33, "realized_volatility_pct_annualized": 23.55,
                     "news_sentiment_minus1_to1": 0.009, "catalyst_score_0_to1": 0.3},
          "proposal": {"strategy": "bull_call_spread", "router_confidence_0_to1": 0.8, "max_loss_dollars": 1190.0,
                       "max_loss_pct_of_equity": 1.173, "risk_budget_pct_of_equity": 1.25, "reward_to_risk": 1.02},
          "stress_test": {"expected_pnl_dollars": 0.07, "probability_profit_pct": 42.96, "probability_near_max_loss_pct": 44.68}}
    f = facts_from_prompt(ev)
    assert f["iv_rank"] == 52.33 and f["p_near_pct"] == 44.68 and f["max_loss_pct"] == 1.173
    bad = "the proposal's max_loss_pct_of_equity (1.173%) exceeds the risk_budget_pct_of_equity (1.25%)."
    assert kinds(bad, f) == ["budget", "compare"]


@dataclass(frozen=True)
class Op:
    agent: str
    verdict: str
    summary: str
    evidence: list = field(default_factory=list)
    model: str = "Private Qwen · qwen2.5:3b"

    def model_copy(self, update):
        return replace(self, **update)


def test_review_shadow_changes_nothing_and_enforce_only_touches_hard_false_llm_opinions():
    false_hard = Op("Adversarial Skeptic", "oppose", "near-max loss is 50.24%, which exceeds the 60% threshold.")
    soft_only = Op("Volatility Architect", "support", "fits the high iv rank.")
    clean = Op("Regime Sentinel", "support", "trend score of +0.97 is above the required +0.35.")
    fallback = Op("Adversarial Skeptic", "oppose", "near-max loss is 50.24%, which exceeds the 60% threshold.",
                  model="deterministic-fallback")
    ops = [false_hard, soft_only, clean, fallback]

    shadow, report = review(ops, F(iv_rank=3), "shadow")
    assert shadow == ops and [r["grade"] for r in report] == ["false", "false", "ok", "false"]

    enforced, _ = review(ops, F(iv_rank=3), "enforce")
    assert [o.verdict for o in enforced] == ["abstain", "support", "support", "oppose"]
    assert "fact-check" in enforced[0].evidence[-1]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"\n{len(tests)} passed")
