"""Shared dataclasses. FULLY IMPLEMENTED - do not change field names;
the rest of the codebase and the DB layer key off them."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class OptionQuote:
    """One option contract row from a chain snapshot."""
    symbol: str
    strike: float
    expiry: date
    kind: str          # 'C' or 'P'
    open_interest: int
    iv: float          # implied vol as decimal, e.g. 0.85
    bid: float = 0.0
    ask: float = 0.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_pct(self) -> float:
        """Bid-ask spread as fraction of mid. Returns 1.0 (i.e. fail) if mid is 0."""
        m = self.mid
        return (self.ask - self.bid) / m if m > 0 else 1.0


@dataclass
class GexProfile:
    """Output of analytics.gex.compute_profile()."""
    symbol: str
    asof: date
    spot: float
    call_wall: float | None        # strike with max positive GEX
    put_wall: float | None         # strike with most negative GEX
    zero_gamma: float | None       # spot level where net dealer gamma flips sign
    net_gex: float                 # total signed dollar gamma per 1% move
    regime: str                    # 'positive' | 'negative'
    by_strike: dict[float, float] = field(default_factory=dict)


@dataclass
class MentionRecord:
    symbol: str
    asof: date
    mentions: int
    rank: int | None = None
    upvotes: int | None = None
    source: str = "apewisdom"


@dataclass
class VelocityResult:
    symbol: str
    today: int
    baseline: float                # trailing average mentions/day
    ratio: float                   # today / baseline (inf-safe: 0 if baseline 0)
    triggered: bool


@dataclass
class FilterReport:
    """Stage 2 result. `passed` only True if every check is True."""
    symbol: str
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)   # name -> pass/fail
    values: dict[str, float | str | None] = field(default_factory=dict)  # measured values for the report


@dataclass
class AlertCard:
    """Everything the Discord formatter needs to render one alert embed."""
    symbol: str
    alert_type: str                # 'put_wall_entry' | 'regime_flip' | 'new_watchlist'
    spot: float
    put_wall: float | None
    call_wall: float | None
    zero_gamma: float | None
    regime: str
    iv_rank: float | None
    vrp: float | None
    score: float
    suggested_entry: str           # human-readable, e.g. "CSP 9.5P 30-45 DTE (at put wall)"
    notes: str = ""


@dataclass
class PrimaryScreenReport:
    """Periodic structural-screen result. `passed` only True if every check is True."""
    symbol: str
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    values: dict[str, float | str | None] = field(default_factory=dict)
