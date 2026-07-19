"""
Market regime read - a transparent, rule-based synthesis of the same
handful of cross-asset relationships every macro desk watches (VIX,
dollar, yields, gold, breadth across global indices), turned into one
plain-language "why is the market moving today" answer.

Deliberately NOT a backtested/calibrated model - these are well-known,
textbook macro relationships (rising VIX = fear, strong dollar =
risk-off for commodities/EM, rising yields = headwind for
duration-sensitive growth equities, gold up = safe-haven demand),
scored and explained using TODAY's actual numbers. This is a real-time
synthesis, not a prediction - see analysis/backtester.py for the
actual validated, backtested trade signals elsewhere in the app. Don't
conflate the two: this tells you what's moving and the classic read on
it, it doesn't claim to know what happens next.
"""


class MarketRegimeEngine:

    VIX_ELEVATED_LEVEL = 25.0
    VIX_MOVE_THRESHOLD_PCT = 3.0
    DXY_MOVE_THRESHOLD_PCT = 0.3
    YIELD_MOVE_THRESHOLD_PCT = 1.5
    GOLD_MOVE_THRESHOLD_PCT = 0.5
    BREADTH_RISK_ON_PCT = 60.0
    BREADTH_RISK_OFF_PCT = 40.0

    @classmethod
    def _score_vix(cls, level, change_pct):

        if change_pct is None:
            return 0, None

        if change_pct <= -cls.VIX_MOVE_THRESHOLD_PCT:
            note = f"VIX down {abs(change_pct):.1f}%"
            if level is not None:
                note += f" to {level:.1f}"
            return 1, note + " - fear easing"

        if change_pct >= cls.VIX_MOVE_THRESHOLD_PCT:
            note = f"VIX up {change_pct:.1f}%"
            if level is not None:
                note += f" to {level:.1f}"
            return -1, note + " - fear rising"

        level_note = f" ({level:.1f})" if level is not None else ""
        return 0, f"VIX roughly flat ({change_pct:+.1f}%){level_note}"

    @classmethod
    def _score_dxy(cls, change_pct):

        if change_pct is None:
            return 0, None

        if change_pct >= cls.DXY_MOVE_THRESHOLD_PCT:
            return -1, f"Dollar (DXY) up {change_pct:.2f}% - headwind for commodities/EM/risk assets"

        if change_pct <= -cls.DXY_MOVE_THRESHOLD_PCT:
            return 1, f"Dollar (DXY) down {change_pct:.2f}% - tailwind for commodities/EM/risk assets"

        return 0, f"Dollar (DXY) roughly flat ({change_pct:+.2f}%)"

    @classmethod
    def _score_yield10(cls, level, change_pct):

        if change_pct is None:
            return 0, None

        bps = round(level * change_pct / 100, 0) if level is not None else None
        bps_text = f" ({bps:+.0f}bps)" if bps is not None else ""

        if change_pct >= cls.YIELD_MOVE_THRESHOLD_PCT:
            note = f"US 10Y yield up {change_pct:.1f}%{bps_text}"
            if level is not None:
                note += f" to {level:.2f}%"
            return -1, note + " - headwind for duration-sensitive growth equities"

        if change_pct <= -cls.YIELD_MOVE_THRESHOLD_PCT:
            note = f"US 10Y yield down {abs(change_pct):.1f}%{bps_text}"
            if level is not None:
                note += f" to {level:.2f}%"
            return 1, note + " - tailwind for duration-sensitive growth equities"

        level_note = f" ({level:.2f}%)" if level is not None else ""
        return 0, f"US 10Y yield roughly flat ({change_pct:+.1f}%){level_note}"

    @classmethod
    def _score_gold(cls, change_pct):

        if change_pct is None:
            return 0, None

        if change_pct >= cls.GOLD_MOVE_THRESHOLD_PCT:
            return -1, f"Gold up {change_pct:.1f}% - safe-haven demand rising"

        if change_pct <= -cls.GOLD_MOVE_THRESHOLD_PCT:
            return 1, f"Gold down {abs(change_pct):.1f}% - safe-haven demand fading"

        return 0, f"Gold roughly flat ({change_pct:+.1f}%)"

    @classmethod
    def _score_breadth(cls, pct_positive):

        if pct_positive is None:
            return 0, None

        if pct_positive >= cls.BREADTH_RISK_ON_PCT:
            return 1, f"{pct_positive:.0f}% of global indices green today - broad participation"

        if pct_positive <= cls.BREADTH_RISK_OFF_PCT:
            return -1, f"only {pct_positive:.0f}% of global indices green today - broad weakness"

        return 0, f"{pct_positive:.0f}% of global indices green today - mixed participation"

    @classmethod
    def classify(cls, *, vix_level=None, vix_change_pct=None, dxy_change_pct=None,
                 yield10_level=None, yield10_change_pct=None, gold_change_pct=None,
                 breadth_pct=None):
        """
        Returns {"label", "score", "factors" (list of plain-language
        notes, one per input that was available), "elevated_vix"}.
        `score` is a simple signed sum, not a probability - each factor
        contributes -1/0/+1 based on the thresholds above.
        """

        factors = []
        score = 0

        for value, note in [
            cls._score_vix(vix_level, vix_change_pct),
            cls._score_dxy(dxy_change_pct),
            cls._score_yield10(yield10_level, yield10_change_pct),
            cls._score_gold(gold_change_pct),
            cls._score_breadth(breadth_pct),
        ]:
            if note is not None:
                score += value
                factors.append(note)

        elevated_vix = vix_level is not None and vix_level >= cls.VIX_ELEVATED_LEVEL

        if elevated_vix:
            label = "🔴 Risk-Off"
        elif score >= 2:
            label = "🟢 Risk-On"
        elif score <= -2:
            label = "🔴 Risk-Off"
        else:
            label = "🟡 Mixed / Neutral"

        return {"label": label, "score": score, "factors": factors, "elevated_vix": elevated_vix}
