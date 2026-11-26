"""Fact-check committee opinions against the numbers the model was given.

The committee prompt hands each model a table of facts (trend score, IV rank,
stress-test probabilities, risk budget) and the policy thresholds. Small models
still write things like "near-max-loss probability of 50.52% exceeds the 60%
limit". This module finds claims of that kind in an opinion's text and checks
them against the same table, so a false statement can be counted, logged or
discounted without a second model call.

Two kinds of finding, deliberately kept apart:

  hard  (compare, value, budget)  A stated number differs from the supplied facts, a
                          comparison between two stated numbers is arithmetically
                          false, or the max loss is said to exceed a risk budget the
                          facts show it is inside. enforce mode acts on these only.
  soft  (iv_level, invented, alignment)  IV called high/low against the wrong end of the
                          0-100 percentile, a threshold the policy does not contain, or
                          "does not fit the regime" when the supplied role check passed.
                          Judgement calls: logged and counted, never acted on.

What it cannot catch: bad reasoning built on correct numbers. "No false claim
found" means exactly that, not "the opinion is sound".

Every pattern requires the number to sit directly beside the thing it
describes. A missed claim costs a little recall; a wrong accusation would make
the whole report untrustworthy, so the extractors err toward silence.

Stdlib only. Drop at backend/agents/factcheck.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Numbers the policy itself states (committee._role_check, risk.py gates, stress policy).
BASE_POLICY = {0.0, 0.2, 0.35, 0.68, 0.7, 25.0, 35.0, 58.0, 60.0, 1.25}
HARD = {"compare", "value", "budget"}


@dataclass
class Claim:
    kind: str     # compare | value | budget (hard) | iv_level | invented | alignment (soft)
    text: str
    status: str   # true | false | unsupported
    why: str = ""
    ctx: str = ""  # the sentence it came from, for auditing


# ── facts ───────────────────────────────────────────────────────────────────

def facts_from_prompt(ev: dict) -> dict:
    """Facts from ReasoningCommittee._evidence() output: exactly what the model saw."""
    m, p, s = ev.get("market", {}), ev.get("proposal", {}), ev.get("stress_test") or {}
    budget = p.get("risk_budget_pct_of_equity")
    return {
        "strategy": p.get("strategy"),
        "trend": m.get("trend_score_minus1_to1"),
        "iv_rank": m.get("iv_rank_percentile_0_to100"),
        "rvol_pct": m.get("realized_volatility_pct_annualized"),
        "news": m.get("news_sentiment_minus1_to1"),
        "catalyst": m.get("catalyst_score_0_to1"),
        "confidence": p.get("router_confidence_0_to1"),
        "rr": p.get("reward_to_risk"),
        "max_loss": p.get("max_loss_dollars"),
        "max_loss_pct": p.get("max_loss_pct_of_equity"),
        "budget_pct": budget,
        "p_profit_pct": s.get("probability_profit_pct"),
        "p_near_pct": s.get("probability_near_max_loss_pct"),
        "exp_pnl": s.get("expected_pnl_dollars"),
        "policy": BASE_POLICY | ({float(budget)} if budget is not None else set()),
    }


def facts_from_record(d: dict) -> dict:
    """Facts rebuilt from a decisions.jsonl record, for replaying history.

    Not exact: the log keeps no news score (parsed from the thesis, 2 decimals)
    and no catalyst score, and equity is implied by the 'risk per trade' gate.
    """
    e, p = d["evidence"], d["plan"]
    gates = {g["name"]: g for g in d.get("gates", [])}
    risk = gates.get("risk per trade")
    pct = budget = None
    if risk:
        pct = float(re.search(r"[\d.]+", risk["observed"]).group())
        budget = float(re.search(r"[\d.]+", risk["limit"]).group())
    news = re.search(r"news sentiment is ([+\-−]?\d+\.\d+)", p.get("thesis", ""), re.I)
    # Only the numbers the committee is told. Gate limits (2.0% daily loss, 8% open risk...) are
    # not in its prompt, and counting them as "policy" would excuse invented thresholds.
    policy = BASE_POLICY | ({budget} if budget is not None else set())
    ml, mp = p.get("max_loss"), p.get("max_profit")
    sim = e.get("simulation_probability_profit")
    return {
        "strategy": p["strategy"],
        "trend": e["trend_score"],
        "iv_rank": e["iv_score"],
        "rvol_pct": e["realized_volatility"] * 100,
        "news": float(news.group(1).replace("−", "-")) if news else None,
        "catalyst": None,
        "confidence": p["confidence"],
        "rr": (mp / ml) if mp is not None and ml else None,
        "max_loss": ml,
        "max_loss_pct": pct,
        "budget_pct": budget,
        "p_profit_pct": sim * 100 if sim is not None else None,
        "p_near_pct": e["simulation_near_max_loss_probability"] * 100 if sim is not None else None,
        "exp_pnl": e.get("simulation_expected_pnl"),
        "policy": policy,
    }


def policy_verdict(role: str, f: dict):
    """What committee._role_check says: True = pass, False = fail, None = cannot tell from this data."""
    s = f["strategy"]
    if role == "Regime Sentinel":
        t = f["trend"]
        return {"bull_call_spread": t >= 0.35, "bear_put_spread": t <= -0.35,
                "iron_condor": abs(t) <= 0.20}.get(s, abs(t) < 0.35)
    if role == "Volatility Architect":
        if s == "iron_condor":
            return f["iv_rank"] >= 58
        if s in ("long_straddle", "long_strangle"):
            return None if f["catalyst"] is None else (f["iv_rank"] <= 25 and f["catalyst"] >= 0.70)
        return True
    if f["max_loss_pct"] is None or f["budget_pct"] is None:
        return None
    return bool(f["max_loss"] > 0 and f["confidence"] >= 0.68 and f["max_loss_pct"] <= f["budget_pct"])


def supports(verdicts: list[str]) -> bool:
    """ReasoningCommittee.supports for a real trade: >=2 support and <=1 oppose."""
    return verdicts.count("support") >= 2 and verdicts.count("oppose") <= 1


# ── claim extraction ────────────────────────────────────────────────────────

_NUM = r"[+-]?\$?\d[\d,]*(?:\.\d+)?"
_REL = {"exceeds": ">", "exceed": ">", "exceeding": ">", "above": ">", "greater than": ">",
        "higher than": ">", "below": "<", "less than": "<", "lower than": "<"}
# "within" is not checked: "within the 1.25% budget" means <=, "within the >=35% requirement"
# means >=, and the text rarely says which.
# Only filler may sit between the left number and the relation, so the number is the thing
# being compared. "1.25% equity and the test shows a profit chance above 35%" must not pair
# 1.25 with 35, and "could exceed" is a hypothetical, not an assertion.
_FILL = (r"(?:[\s,()%:]|\b(?:which|that|is|are|was|were|also|remains?|not|still|only|just|significantly|well|"
         r"slightly|far|much|comfortably|clearly|barely|easily|marginally)\b|n't)*?")
_CMP = re.compile(
    rf"(?P<a>{_NUM})\s*(?P<au>%|x)?(?P<gap>{_FILL})\b(?P<rel>{'|'.join(_REL)})\b"
    rf"(?P<mid>[^\d;]{{0,45}}?)(?P<b>{_NUM})\s*(?P<bu>%|x)?")
_THRESHOLD_KEYS = {"trend", "iv_rank", "confidence", "p_profit_pct", "p_near_pct"}
_NEG = re.compile(r"\b(?:not|never|neither|nor)\b|n't")
_THRESH_BEFORE = re.compile(r"\b(?:required|minimum|maximum|threshold|limit|policy|target|requires?)\s*(?:the\s+)?$|"
                            r"\b(?:required|minimum|maximum)\b[^.;\d]{0,20}$")
_RANGE = re.compile(r"\s*(?:-|to)\s*\d")

# (name, label, fact key, tolerance, number must carry a %). Between label and number only filler
# is allowed, so "probability of profit and a 49.96% ..." is not read as a value for it.
_VGAP = (r"(?:[\s:=()$]|\b(?:of|is|was|are|at|about|around|roughly|approximately|only|just|a|an|the|"
         r"stands|equals?|to|being)\b)*?")
_VALUES = [
    ("trend score", r"trend(?: score)?(?:_minus1_to1)?", "trend", 0.02, False),
    ("IV rank", r"\biv(?:[_ ]rank|[_ ]score)?(?:[_ ]percentile)?(?:_0_to100)?", "iv_rank", 1.0, False),   # "74th percentile" for 74.7 truncates
    ("realized volatility", r"realized[_ ]vol(?:atility)?(?:_pct_annualized)?", "rvol_pct", 0.6, True),
    ("near-max-loss probability", r"near[- ]max(?:imum)?[- ]loss(?:[_ ]probability)?(?:_pct)?", "p_near_pct", 0.6, True),
    ("probability of profit",
     r"(?:probability(?:_profit)?(?: of)? profit|profit probability|chance of profit)(?:_pct)?", "p_profit_pct", 0.6, True),
    ("expected P&L", r"expected[_ ](?:pnl|p&l|profit)(?:_dollars)?(?!\s+probab)", "exp_pnl", 1.5, False),
    ("reward-to-risk", r"reward[- _]to[- _]risk(?: ratio)?", "rr", 0.012, False),
    ("risk budget", r"risk[_ ]budget(?:_pct_of_equity)?", "budget_pct", 0.011, True),
    ("news sentiment", r"news[_ ]sentiment(?:_minus1_to1)?", "news", 0.011, False),
    ("router confidence", r"(?:router[_ ])?confidence(?:_0_to1)?", "confidence", 0.006, False),
]
_VALUE_RES = [(n, re.compile(rf"(?:{lab}){_VGAP}(?P<n>{_NUM})(?P<u>\s*%)?", re.I), k, t, pct)
              for n, lab, k, t, pct in _VALUES]
_MAXLOSS_PCT = re.compile(
    r"max(?:imum)?[_ ]loss(?:_pct_of_equity|[ ]percent(?:age)?)[^\d;]{0,15}(?P<n>\d+(?:\.\d+)?)|"
    r"max(?:imum)?[_ ]loss[^\d;$]{0,15}(?P<m>\d+(?:\.\d+)?)\s*%\s*of\s*(?:the\s*)?equity", re.I)
_MAXLOSS_USD = re.compile(
    r"max_loss_dollars\W{0,4}(?P<n>\d[\d,]*(?:\.\d+)?)(?!\.\d|\d)|"
    r"max(?:imum)?[_ ]loss\W{0,6}\$\s*(?P<m>\d[\d,]*(?:\.\d+)?)(?!\s*%|\.\d|\d)", re.I)

# The adjective must modify IV directly: "high IV", "IV rank is high". "The IV rank and high
# realized volatility ..." is about realized volatility and must not match.
_IV_RE = re.compile(
    r"\b(?P<pre>high|elevated|low|depressed)\s+(?:implied volatility|iv)\b|"
    r"\b(?:implied volatility|iv)(?:[ _]rank|[ _]score)?(?:\s+of\s+[\d.]+)?\s+(?:is|are|was|remains|appears|seems)\s+"
    r"(?:a\s+)?(?:relatively\s+|quite\s+|very\s+|fairly\s+)?(?P<post>high|elevated|low|depressed)\b")
# Asserted (not hedged) "the loss exceeds the risk budget". The router sizes every trade inside the
# budget, so when the facts say otherwise this is a plain falsehood. "meet or exceed" is not an assertion.
_BUDGET = re.compile(r"\b(?:exceeds?|exceeded|exceeding|violates?|violating|breach(?:es|ed|ing)?)\b[^.;\d]{0,25}"
                     r"\b(?:risk[_ ]budget\w*|equity budget|(?:max(?:imum)? )?loss budget)\b")   # per-trade budget only
_HEDGE = re.compile(r"\b(?:could|may|might|would|potentially|lead|leading|risk of|chance|possible|possibly|if|"
                    r"whether|can|or)\b|n't|\bnot\b")
# "The bear put spread does not align with the bearish regime." The prompt includes role_check.passed,
# so when policy_verdict says the regime check passes, this contradicts the supplied evidence. Soft:
# the sentence has no number to test, so the judgement lives in the role check, not in a rule about words.
_MISALIGN = re.compile(r"(?:\bnot|n't|\bno longer)\s+(?:clearly\s+|well\s+|fully\s+)?(?:align(?:ed)?|fits?|consistent|support(?:ed|s)?)\b|"
                       r"\bmisaligned\b|\binconsistent with\b|\bconflicts? with\b|\bcontradicts?\b")
_INVENTED = re.compile(
    r"\b(?:typical(?:ly)?|generally(?: \w+)?|usual(?:ly)?|standard|industry|common(?:ly)?|conventional)\b"
    r"[^.;\d]{0,40}?\b(?:threshold|limit|cutoff|maximum|minimum|benchmark|norm|budget)\b[^.;\d]{0,20}?(?P<n>\d+(?:\.\d+)?)")
_NAMED_THRESHOLD = re.compile(
    r"\b(?:threshold|limit|cutoff|required (?:minimum|maximum))\b[^.;\d]{0,12}?(?:of|at|is)\s*(?P<n>[+-]?\d+(?:\.\d+)?)")


def _f(s: str) -> float:
    return float(s.replace(",", "").replace("$", ""))


def _unit(tok: str, u) -> str:
    return "$" if "$" in tok else (u or "")


def _sentences(parts: list[str]):
    for part in parts:
        text = part.replace("−", "-").replace("–", "-").lower()
        yield from (s for s in re.split(r"\.\s+|;\s+|\n", text) if s.strip())


def check(parts: list[str], facts: dict) -> list[Claim]:
    """Every checkable claim in an opinion's summary + evidence strings."""
    out: list[Claim] = []
    seen: set[str] = set()

    def add(kind, text, status, why, ctx):
        key = kind + text
        if key not in seen:
            seen.add(key)
            out.append(Claim(kind, text, status, why, ctx[:200]))

    for s in _sentences(parts):
        # 1. comparisons: is "A above B" arithmetically true?
        for m in _CMP.finditer(s):
            if _RANGE.match(s[m.end():]):
                continue
            ua, ub = _unit(m["a"], m["au"]), _unit(m["b"], m["bu"])
            if "$" in (ua, ub) and ua != ub:
                continue
            a, b, op = _f(m["a"]), _f(m["b"]), _REL[m["rel"]]
            if a < 0 and b < 0:
                continue          # "-0.58 is above the -0.35 threshold" is loose wording for 'beyond it'
            holds = a > b if op == ">" else a < b
            claimed = not _NEG.search(m["gap"])
            add("compare", m.group(0).strip(), "true" if holds == claimed else "false",
                f"{m['a']} {'is' if holds else 'is not'} {m['rel']} {m['b']}", s)

        # 2. stated values vs the facts table
        for name, rx, key, tol, need_pct in _VALUE_RES:
            for m in rx.finditer(s):
                actual = facts.get(key)
                if actual is None or (need_pct and not m["u"]):
                    continue
                if _THRESH_BEFORE.search(s[max(0, m.start() - 30):m.start()]):
                    continue                      # "the minimum required trend score of +0.35" names a threshold
                if _RANGE.match(s[m.end():]):
                    continue                      # "a typical risk budget of 1% to 2%"
                said = _f(m["n"])
                slack = 0.5 * 10 ** -len(m["n"].partition(".")[2])   # rounding to the written precision is not an error
                if key == "confidence" and said > 1:
                    said, slack = said / 100, slack / 100
                signed = m["n"].lstrip("$")[0] in "+-"
                diff = abs(said - actual) if signed else abs(abs(said) - abs(actual))
                ok = diff <= tol + slack
                if not ok and key in _THRESHOLD_KEYS and any(abs(abs(said) - q) < 0.005 for q in facts.get("policy", ())):
                    continue                      # equals a policy number: most likely naming the threshold
                add("value", m.group(0).strip(), "true" if ok else "false",
                    f"{name}: said {said:g}, actual {actual:g}", s)
        for rx, key, tol in ((_MAXLOSS_PCT, "max_loss_pct", 0.02), (_MAXLOSS_USD, "max_loss", 1.0)):
            for m in rx.finditer(s):
                actual = facts.get(key)
                raw = m["n"] or m["m"]
                if actual is None or re.match(r"\s*budget", s[m.end():]):
                    continue
                ok = abs(_f(raw) - actual) <= tol
                add("value", m.group(0).strip(), "true" if ok else "false",
                    f"{key}: said {_f(raw):g}, actual {actual:g}", s)

        # 3. "exceeds the risk budget" against the max-loss gate
        pct, bud = facts.get("max_loss_pct"), facts.get("budget_pct")
        if pct is not None and bud is not None and pct <= bud + 0.005:
            for m in _BUDGET.finditer(s):
                if not _HEDGE.search(s[max(0, m.start() - 70):m.start()]):
                    add("budget", m.group(0).strip(), "false",
                        f"said max loss exceeds the budget; actual {pct:g}% vs {bud:g}%", s)

        # 3b. "does not fit the regime" while the supplied regime role check passes
        if re.search(r"\b(?:regime|trend)\b", s) and policy_verdict("Regime Sentinel", facts) is True:
            m = _MISALIGN.search(s)
            if m:
                add("alignment", m.group(0).strip(), "false",
                    f"said the structure does not fit the regime; the role check passes "
                    f"({facts['strategy']}, trend {facts['trend']:+.2f})", s)

        # 4. IV level against the 0-100 percentile
        iv = facts.get("iv_rank")
        for m in _IV_RE.finditer(s) if iv is not None else ():
            word = m["pre"] or m["post"]
            if _NEG.search(s[max(0, m.start() - 15):m.start()]):
                continue
            high = word in ("high", "elevated")
            bad = iv < 30 if high else iv > 70
            add("iv_level", m.group(0).strip(), "false" if bad else "true",
                f"called IV {word}; IV rank is {iv:.1f} of 100", s)

        # 5. authority the policy does not contain
        named: set[float] = set()
        for rx, why in ((_INVENTED, "'{n:g}' is presented as a standard, not a policy threshold"),
                        (_NAMED_THRESHOLD, "threshold {n:g} is not in the policy")):
            for m in rx.finditer(s):
                n = abs(float(m["n"]))
                if n in named or any(abs(n - p) < 0.005 for p in facts.get("policy", BASE_POLICY)):
                    continue              # already reported, or a real policy number however it is introduced
                named.add(n)
                add("invented", m.group(0).strip(), "unsupported", why.format(n=n), s)
    return out


def grade(claims: list[Claim], strict: bool = False) -> str:
    """false > unsupported > ok > unchecked. strict: only hard findings count."""
    use = [c for c in claims if c.kind in HARD] if strict else claims
    kinds = {c.status for c in use}
    return "false" if "false" in kinds else "unsupported" if "unsupported" in kinds else "ok" if kinds else "unchecked"


def review(opinions: list, facts: dict, mode: str = "shadow") -> tuple[list, list[dict]]:
    """Check each AgentOpinion-like object. Returns (opinions, per-opinion report).

    shadow: opinions are returned unchanged. enforce: a model-written opinion with a
    hard false claim is downgraded to abstain, because an opinion that misreads the
    supplied numbers is not evidence for or against the trade. Soft findings never
    change a verdict. Deterministic-fallback opinions are never touched.
    """
    report, out = [], []
    for o in opinions:
        claims = check([o.summary, *o.evidence], facts)
        g = grade(claims, strict=True)
        report.append({"agent": o.agent, "grade": grade(claims), "verdict": o.verdict,
                       "false": [c.why for c in claims if c.status == "false"]})
        if mode == "enforce" and g == "false" and o.model != "deterministic-fallback" and o.verdict != "abstain":
            o = o.model_copy(update={"verdict": "abstain",
                                     "evidence": [*o.evidence, "[fact-check: opinion misstated the supplied evidence]"][:4]})
        out.append(o)
    return out, report
