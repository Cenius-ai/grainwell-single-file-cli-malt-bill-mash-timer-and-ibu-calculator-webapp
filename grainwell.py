"""Grainwell - a malt bill builder, mash timer and hop bitterness calculator.

Grainwell is a single-file, standard-library-only command line tool for
all-grain brewers working in metric:

    grainwell bill   --add "name,kg,lovibond" ... --volume L
    grainwell mash strike --ratio L/kg --target C [--grain-temp C]
    grainwell mash timer  --step "name,target_C,minutes" ... [--plan-only]
    grainwell ibu    --hop "variety,alpha%,grams,minutes" ... --volume L --gravity 1.0xx

Everything is computed in memory; nothing is stored, nothing is networked and
no third-party package is required.  Run it as ``python3 grainwell.py``.

Design tokens (the committed design direction for this tool) live in THEME
below: system monospace, terminal-dark surface, compact density, aligned
columns and one accent (OKLCH hue 202 / #008e98) used only as a reinforcing
marker next to a text signal - never as the only signal.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

__version__ = "1.0.0"
PROG = "grainwell"

# --------------------------------------------------------------------------
# Design tokens: the single source of truth for the tool's voice and colour.
# Neutral ramp is a terminal-dark set; the accent is used for the *primary*
# result marker only and is always paired with a textual marker (">") so
# colour is never the only signal.  The beer swatch is a measured colour, not
# a theme colour, and is the only place a background fill is drawn.
# --------------------------------------------------------------------------
THEME = {
    "accent_hex": "#008e98",          # OKLCH(0.58 0.12 202) expressed as hex
    "accent_oklch": "oklch(0.58 0.12 202)",
    "surface": "terminal-dark",
    "density": "compact",
    "box_rule": "-",
    "head_rule": "=",
    "marker": ">",
    "type_family": "system monospace",
}

# Exit codes (documented in --help and the README).
EXIT_OK = 0
EXIT_INVALID = 2
EXIT_INTERRUPTED = 130

# --------------------------------------------------------------------------
# Units and ranges
# --------------------------------------------------------------------------
KG_TO_LB = 2.2046226218
L_TO_US_GAL = 0.2641720524

VOLUME_RANGE = (0.5, 200.0)
GRAIN_WEIGHT_RANGE = (0.0, 1000.0)      # exclusive lower bound
LOVIBOND_RANGE = (0.0, 600.0)
ALPHA_RANGE = (0.0, 25.0)
HOP_WEIGHT_RANGE = (0.0, 10000.0)       # exclusive lower bound
HOP_MINUTES_RANGE = (0.0, 300.0)
GRAVITY_RANGE = (1.000, 1.150)
RATIO_RANGE = (1.5, 5.0)
STRIKE_TARGET_RANGE = (30.0, 80.0)
GRAIN_TEMP_RANGE = (0.0, 40.0)
STEP_TARGET_RANGE = (30.0, 80.0)
STEP_MINUTES_RANGE = (0.0, 600.0)       # exclusive lower bound
EBC_FACTOR = 1.97


class InvalidInput(Exception):
    """Raised for a user-facing validation failure; the CLI exits 2."""


# --------------------------------------------------------------------------
# Shared formatting helpers
# --------------------------------------------------------------------------
def fmt(value: float, places: int = 1) -> str:
    """Format a float with a fixed number of decimals (no locale surprises)."""
    return f"{value:.{places}f}"


def rounded_shares(values: Sequence[float], places: int = 1) -> List[float]:
    """Split 100 % across ``values`` proportionally, rounding to ``places``.

    Uses the largest-remainder method so the printed shares add up to exactly
    100.0 instead of 99.9 or 100.1.
    """
    if not values:
        return []
    total = sum(values)
    if total <= 0:
        return [0.0] * len(values)
    scale = 10 ** places
    exact = [v / total * 100.0 * scale for v in values]
    floors = [math.floor(e) for e in exact]
    remainder = int(round(100.0 * scale - sum(floors)))
    order = sorted(range(len(values)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[:remainder]:
        floors[i] += 1
    return [f / scale for f in floors]


def table_rule(width: int, char: str = "-") -> str:
    return char * max(1, width)


# --------------------------------------------------------------------------
# Terminal capability detection
# --------------------------------------------------------------------------
def colour_enabled(stream: object = None, env: Optional[dict] = None) -> bool:
    """True when ANSI colour may be written to ``stream``.

    Honours NO_COLOR (any non-empty value disables colour) and refuses to emit
    escapes when the stream is not a terminal, so piped output stays plain.
    """
    env = os.environ if env is None else env
    if env.get("NO_COLOR"):
        return False
    stream = sys.stdout if stream is None else stream
    isatty = getattr(stream, "isatty", None)
    try:
        if not callable(isatty) or not isatty():
            return False
    except (ValueError, OSError):  # closed stream
        return False
    term = str(env.get("TERM", "")).strip().lower()
    if term in ("", "dumb"):
        return False
    return True


def truecolour_enabled(stream: object = None, env: Optional[dict] = None) -> bool:
    """True when the terminal advertises 24-bit colour (COLORTERM / TERM)."""
    if not colour_enabled(stream, env):
        return False
    env = os.environ if env is None else env
    colorterm = str(env.get("COLORTERM", "")).strip().lower()
    term = str(env.get("TERM", "")).strip().lower()
    return colorterm in ("truecolor", "24bit") or "truecolor" in term or "direct" in term


# --------------------------------------------------------------------------
# F1 - malt bill and Morey colour
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Fermentable:
    name: str
    weight_kg: float
    lovibond: float
    share_pct: float = 0.0

    @property
    def weight_lb(self) -> float:
        return self.weight_kg * KG_TO_LB


@dataclass(frozen=True)
class MaltBill:
    batch_volume_l: float
    fermentables: Tuple[Fermentable, ...]

    @property
    def total_grist_kg(self) -> float:
        return sum(f.weight_kg for f in self.fermentables)

    @property
    def mcu(self) -> float:
        """Malt colour units: sum(lb x lovibond) / batch gallons."""
        if self.batch_volume_l <= 0:
            return 0.0
        gallons = self.batch_volume_l * L_TO_US_GAL
        return sum(f.weight_lb * f.lovibond for f in self.fermentables) / gallons

    @property
    def srm(self) -> float:
        """Morey: SRM = 1.4922 x MCU ^ 0.6859 (0 MCU means no colour)."""
        return morey_srm(self.mcu)

    @property
    def ebc(self) -> float:
        return self.srm * EBC_FACTOR

    @property
    def colour_word(self) -> str:
        return colour_word(self.srm)

    @property
    def srgb_hex(self) -> str:
        r, g, b = srm_to_rgb(self.srm)
        return "#{:02X}{:02X}{:02X}".format(r, g, b)


def morey_srm(mcu: float) -> float:
    """Morey colour in SRM from malt colour units (no colour below 0 MCU)."""
    if mcu <= 0:
        return 0.0
    return 1.4922 * mcu ** 0.6859


_COLOUR_WORDS: Tuple[Tuple[float, str], ...] = (
    (2.0, "pale straw"),
    (3.5, "straw"),
    (5.0, "pale gold"),
    (7.0, "gold"),
    (11.0, "amber"),
    (14.0, "deep amber"),
    (17.0, "copper"),
    (22.0, "brown"),
    (27.0, "dark brown"),
    (40.0, "very dark brown"),
    (float("inf"), "black"),
)


def colour_word(srm: float) -> str:
    """Plain-word descriptor for an SRM value (beat the number, then name it)."""
    for limit, word in _COLOUR_WORDS:
        if srm <= limit:
            return word
    return "black"  # pragma: no cover - the last entry is infinite


# Anchors of the sRGB approximation, sampled from a glass of beer viewed at
# 1 cm: each SRM step scatters the same fraction of light, so the channels
# fall away exponentially from their pale-beer values, green fastest and blue
# slowest.  Values are interpolated linearly between the anchors, which keeps
# dark beers a dark brown rather than a saturated red.
_SRGB_ANCHORS: Tuple[Tuple[float, Tuple[int, int, int]], ...] = (
    (0.0, (255, 255, 248)),
    (1.0, (255, 252, 222)),
    (2.0, (255, 246, 186)),
    (4.0, (252, 226, 122)),
    (6.0, (246, 195, 68)),
    (8.0, (233, 160, 30)),
    (10.0, (217, 130, 17)),
    (13.0, (196, 96, 12)),
    (17.0, (176, 68, 10)),
    (20.0, (158, 50, 8)),
    (24.0, (130, 30, 6)),
    (30.0, (96, 18, 4)),
    (40.0, (55, 12, 3)),
    (50.0, (38, 8, 2)),
    (60.0, (26, 6, 2)),
)


def srm_to_rgb(srm: float) -> Tuple[int, int, int]:
    """Approximate the on-screen colour of a beer of the given SRM."""
    srm = max(0.0, srm)
    anchors = _SRGB_ANCHORS
    if srm >= anchors[-1][0]:
        return anchors[-1][1]
    for (x0, c0), (x1, c1) in zip(anchors, anchors[1:]):
        if srm <= x1:
            span = x1 - x0
            t = 0.0 if span == 0 else (srm - x0) / span
            return tuple(  # type: ignore[return-value]
                int(round(c0[i] + (c1[i] - c0[i]) * t)) for i in range(3)
            )
    return anchors[-1][1]  # pragma: no cover - guarded by the check above


def build_bill(volume_l: Optional[float], entries: Sequence[Fermentable]) -> MaltBill:
    """Validate a batch volume plus its fermentables and return the bill.

    This is the in-process handler behind ``/api/malt-bill``.
    """
    volume = parse_range_float("--volume", volume_l, *VOLUME_RANGE, "L", 1)
    if not entries:
        raise InvalidInput(
            "a malt bill needs at least one fermentable; add one with "
            '--add "Pilsner,5,1.8"'
        )
    return MaltBill(batch_volume_l=volume, fermentables=tuple(entries))


def colour_report(
    bill: MaltBill, stream: object = None, env: Optional[dict] = None
) -> dict:
    """The ``/api/malt-bill/color`` handler.

    Returns the SRM/EBC pair, the sRGB hex for the swatch and whether the
    current terminal can actually render it, so a view can draw the band or
    fall back to the word descriptor plus the hex.
    """
    rgb = srm_to_rgb(bill.srm)
    return {
        "srm": round(bill.srm, 1),
        "ebc": round(bill.ebc, 1),
        "colour_word": bill.colour_word,
        "rgb": list(rgb),
        "srgb_hex": "#{:02X}{:02X}{:02X}".format(*rgb),
        "colour": colour_enabled(stream, env),
        "truecolour": truecolour_enabled(stream, env),
    }


def bill_shares(bill: MaltBill) -> Tuple[Fermentable, ...]:
    """Return the bill's fermentables with their percent-of-bill filled in."""
    shares = rounded_shares([f.weight_kg for f in bill.fermentables])
    return tuple(
        Fermentable(f.name, f.weight_kg, f.lovibond, share)
        for f, share in zip(bill.fermentables, shares)
    )


# --------------------------------------------------------------------------
# F2 - strike temperature table and mash schedule
# --------------------------------------------------------------------------
# Tabulated strike-water temperatures in degC, indexed ratio (L/kg) then
# target mash temperature (degC), for a 20 degC grain bed and a single-infusion
# mash tun that has been pre-heated.  Generated from
#     strike = 0.4172 / ratio * (target - 20) + target
# where 0.4172 L.kg/degC is the specific heat of malt expressed the way a
# brewer measures it (0.2 qt/lb equivalents scaled to litres per kilogram).
GRAIN_TEMP_REFERENCE = 20.0
GRAIN_HEAT_FACTOR = 0.4172
STRIKE_TARGETS = (55.0, 60.0, 65.0, 70.0, 75.0)

STRIKE_TABLE: Tuple[Tuple[str, float, Tuple[float, ...]], ...] = (
    ("2.0", 2.0, (62.3, 68.3, 74.4, 80.4, 86.5)),
    ("2.5", 2.5, (60.8, 66.7, 72.5, 78.3, 84.2)),
    ("3.0", 3.0, (59.9, 65.6, 71.3, 77.0, 82.6)),
    ("3.5", 3.5, (59.2, 64.8, 70.4, 76.0, 81.6)),
    ("4.0", 4.0, (58.7, 64.2, 69.7, 75.2, 80.7)),
)


@dataclass(frozen=True)
class StrikeLookup:
    ratio_l_per_kg: float
    target_temp_c: float
    grain_temp_c: float
    strike_temp_c: float
    row_ratio: float
    row_source: str          # "tabulated" | "interpolated" | "extrapolated"
    grain_correction_c: float


def _interp(x: float, xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, str]:
    """Piecewise-linear lookup; returns (value, source label).

    Values inside the grid are interpolated between the two neighbouring
    cells; values outside it follow the trend of the outermost pair.
    """
    if x <= xs[0]:
        span = xs[1] - xs[0]
        if span == 0:  # pragma: no cover - the table is strictly increasing
            return ys[0], "tabulated"
        slope = (ys[1] - ys[0]) / span
        return ys[0] + slope * (x - xs[0]), "extrapolated" if x < xs[0] else "tabulated"
    if x >= xs[-1]:
        span = xs[-1] - xs[-2]
        slope = (ys[-1] - ys[-2]) / span
        label = "tabulated" if x == xs[-1] else "extrapolated"
        return ys[-1] + slope * (x - xs[-1]), label
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            if x == xs[i]:
                return ys[i], "tabulated"
            if x == xs[i + 1]:
                return ys[i + 1], "tabulated"
            span = xs[i + 1] - xs[i]
            if span == 0:  # pragma: no cover - the table is strictly increasing
                return ys[i], "tabulated"
            t = (x - xs[i]) / span
            return ys[i] + (ys[i + 1] - ys[i]) * t, "interpolated"
    return ys[-1], "tabulated"  # pragma: no cover


def strike_temperature(
    ratio_l_per_kg: float, target_temp_c: float, grain_temp_c: float = GRAIN_TEMP_REFERENCE
) -> StrikeLookup:
    """Look up strike water temperature from the table, then correct for grain.

    The tabulated cells are for a 20 degC grain bed; a colder grain bed needs
    hotter strike water, scaled by the water:grain ratio.
    """
    if not RATIO_RANGE[0] <= ratio_l_per_kg <= RATIO_RANGE[1]:
        raise InvalidInput(
            "invalid --ratio {}: must be between {} and {} L/kg".format(
                fmt(ratio_l_per_kg, 2), fmt(RATIO_RANGE[0], 2), fmt(RATIO_RANGE[1], 2)
            )
        )
    if not STRIKE_TARGET_RANGE[0] <= target_temp_c <= STRIKE_TARGET_RANGE[1]:
        raise InvalidInput(
            "invalid --target {}: must be between {} and {} C".format(
                fmt(target_temp_c, 1),
                fmt(STRIKE_TARGET_RANGE[0], 0),
                fmt(STRIKE_TARGET_RANGE[1], 0),
            )
        )
    if not GRAIN_TEMP_RANGE[0] <= grain_temp_c <= GRAIN_TEMP_RANGE[1]:
        raise InvalidInput(
            "invalid --grain-temp {}: must be between {} and {} C".format(
                fmt(grain_temp_c, 1),
                fmt(GRAIN_TEMP_RANGE[0], 0),
                fmt(GRAIN_TEMP_RANGE[1], 0),
            )
        )

    # Interpolate along the target-temperature axis of every ratio row, then
    # between the rows themselves: two linear passes over a grid whose cells
    # are linear in target temperature.
    values = [_interp(target_temp_c, STRIKE_TARGETS, cells)[0] for _, _, cells in STRIKE_TABLE]
    row_ratios = [row[1] for row in STRIKE_TABLE]
    base, _row_label = _interp(ratio_l_per_kg, row_ratios, values)
    in_grid = (
        row_ratios[0] <= ratio_l_per_kg <= row_ratios[-1]
        and STRIKE_TARGETS[0] <= target_temp_c <= STRIKE_TARGETS[-1]
    )
    on_cell = ratio_l_per_kg in row_ratios and target_temp_c in STRIKE_TARGETS
    source = "tabulated" if on_cell else ("interpolated" if in_grid else "extrapolated")

    correction = GRAIN_HEAT_FACTOR / ratio_l_per_kg * (GRAIN_TEMP_REFERENCE - grain_temp_c)
    nearest_row = min(row_ratios, key=lambda r: abs(r - ratio_l_per_kg))
    return StrikeLookup(
        ratio_l_per_kg=ratio_l_per_kg,
        target_temp_c=target_temp_c,
        grain_temp_c=grain_temp_c,
        strike_temp_c=base + correction,
        row_ratio=nearest_row,
        row_source=source,
        grain_correction_c=correction,
    )


@dataclass(frozen=True)
class MashStep:
    name: str
    target_temp_c: float
    hold_minutes: float
    strike_temp_c: float = 0.0
    start_offset_min: float = 0.0

    @property
    def end_offset_min(self) -> float:
        return self.start_offset_min + self.hold_minutes


@dataclass(frozen=True)
class MashSchedule:
    ratio_l_per_kg: float
    grain_temp_c: float
    steps: Tuple[MashStep, ...]

    @property
    def total_minutes(self) -> float:
        return sum(s.hold_minutes for s in self.steps)


def build_schedule(
    steps: Sequence[MashStep],
    ratio_l_per_kg: float = 3.0,
    grain_temp_c: float = GRAIN_TEMP_REFERENCE,
) -> MashSchedule:
    """Validate steps and attach strike temperatures plus running-clock offsets."""
    if not steps:
        raise InvalidInput("no mash steps given")
    elapsed = 0.0
    built: List[MashStep] = []
    for step in steps:
        if not STEP_TARGET_RANGE[0] <= step.target_temp_c <= STEP_TARGET_RANGE[1]:
            raise InvalidInput(
                'invalid --step "{}": target temperature {} C must be between '
                "{} and {} C".format(
                    step.name,
                    fmt(step.target_temp_c, 1),
                    fmt(STEP_TARGET_RANGE[0], 0),
                    fmt(STEP_TARGET_RANGE[1], 0),
                )
            )
        if not STEP_MINUTES_RANGE[0] < step.hold_minutes <= STEP_MINUTES_RANGE[1]:
            raise InvalidInput(
                'invalid --step "{}": hold time {} must be more than 0 and at '
                "most {} minutes".format(
                    step.name, fmt(step.hold_minutes, 2), fmt(STEP_MINUTES_RANGE[1], 0)
                )
            )
        lookup = strike_temperature(ratio_l_per_kg, step.target_temp_c, grain_temp_c)
        built.append(
            MashStep(
                name=step.name,
                target_temp_c=step.target_temp_c,
                hold_minutes=step.hold_minutes,
                strike_temp_c=lookup.strike_temp_c,
                start_offset_min=elapsed,
            )
        )
        elapsed += step.hold_minutes
    return MashSchedule(ratio_l_per_kg, grain_temp_c, tuple(built))


def mmss(seconds: float) -> str:
    """Format a duration as mm:ss (never negative, never NaN)."""
    seconds = max(0.0, seconds)
    total = int(math.ceil(seconds - 1e-9))
    return "{:02d}:{:02d}".format(total // 60, total % 60)


# --------------------------------------------------------------------------
# F3 - Tinseth bitterness
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class HopAddition:
    variety: str
    alpha_acid_pct: float
    weight_g: float
    boil_minutes: float
    utilization_pct: float = 0.0
    ibu: float = 0.0


@dataclass(frozen=True)
class IbuTotal:
    batch_volume_l: float
    boil_gravity: float
    additions: Tuple[HopAddition, ...]

    @property
    def total_ibu(self) -> float:
        return sum(a.ibu for a in self.additions)

    @property
    def short_boil(self) -> bool:
        return any(a.boil_minutes < 20 for a in self.additions)


def tinseth_utilization(boil_gravity: float, boil_minutes: float) -> float:
    """Tinseth utilization as a fraction (0 at zero boil time, never NaN)."""
    if boil_minutes <= 0:
        return 0.0
    bigness = 1.65 * 0.000125 ** (boil_gravity - 1.0)
    boil_time_factor = (1.0 - math.exp(-0.04 * boil_minutes)) / 4.15
    return bigness * boil_time_factor


def ibu_for_addition(
    weight_g: float, alpha_acid_pct: float, boil_minutes: float,
    batch_volume_l: float, boil_gravity: float,
) -> Tuple[float, float]:
    """Return (utilization percent, IBU) for one hop charge."""
    utilization = tinseth_utilization(boil_gravity, boil_minutes)
    ibu = weight_g * (alpha_acid_pct / 100.0) * utilization * 1000.0 / batch_volume_l
    return utilization * 100.0, ibu


def build_ibu_total(
    additions: Sequence[HopAddition], batch_volume_l: float, boil_gravity: float
) -> IbuTotal:
    """Validate the batch inputs and fill in utilization and IBU per addition."""
    if not VOLUME_RANGE[0] <= batch_volume_l <= VOLUME_RANGE[1]:
        raise InvalidInput(
            "invalid --volume {}: must be between {} and {} L".format(
                fmt(batch_volume_l, 2), fmt(VOLUME_RANGE[0], 1), fmt(VOLUME_RANGE[1], 0)
            )
        )
    if not GRAVITY_RANGE[0] <= boil_gravity <= GRAVITY_RANGE[1]:
        raise InvalidInput(
            "invalid --gravity {}: must be between {} and {} (boil gravity, "
            "e.g. 1.050)".format(
                fmt(boil_gravity, 3), fmt(GRAVITY_RANGE[0], 3), fmt(GRAVITY_RANGE[1], 3)
            )
        )
    computed = []
    for add in additions:
        util_pct, ibu = ibu_for_addition(
            add.weight_g, add.alpha_acid_pct, add.boil_minutes, batch_volume_l, boil_gravity
        )
        computed.append(
            HopAddition(
                variety=add.variety,
                alpha_acid_pct=add.alpha_acid_pct,
                weight_g=add.weight_g,
                boil_minutes=add.boil_minutes,
                utilization_pct=util_pct,
                ibu=ibu,
            )
        )
    return IbuTotal(batch_volume_l, boil_gravity, tuple(computed))


def ibu_shares(total: IbuTotal) -> List[float]:
    return rounded_shares([a.ibu for a in total.additions])


# --------------------------------------------------------------------------
# Input parsing (each error names the offending flag and its accepted range)
# --------------------------------------------------------------------------
def parse_fermentable(raw: str) -> Tuple[str, float, float]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 3 or not parts[0]:
        raise InvalidInput(
            'invalid --add "{}": expected "name,kg,lovibond", for example '
            '"Pilsner,5,1.8"'.format(raw)
        )
    name, weight_text, lovibond_text = parts
    try:
        weight = float(weight_text)
    except ValueError:
        weight = float("nan")
    if not math.isfinite(weight) or not GRAIN_WEIGHT_RANGE[0] < weight <= GRAIN_WEIGHT_RANGE[1]:
        raise InvalidInput(
            'invalid --add "{}": weight "{}" must be a number of kg greater '
            "than {} and no more than {}".format(
                raw, weight_text, fmt(GRAIN_WEIGHT_RANGE[0], 0), fmt(GRAIN_WEIGHT_RANGE[1], 0)
            )
        )
    try:
        lovibond = float(lovibond_text)
    except ValueError:
        lovibond = float("nan")
    if not math.isfinite(lovibond) or not LOVIBOND_RANGE[0] <= lovibond <= LOVIBOND_RANGE[1]:
        raise InvalidInput(
            'invalid --add "{}": lovibond "{}" must be a number between {} and '
            "{} (e.g. 1.8 for pilsner, 60 for crystal)".format(
                raw, lovibond_text, fmt(LOVIBOND_RANGE[0], 0), fmt(LOVIBOND_RANGE[1], 0)
            )
        )
    return name, weight, lovibond


def parse_step(raw: str) -> MashStep:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 3 or not parts[0]:
        raise InvalidInput(
            'invalid --step "{}": expected "name,target_C,minutes", for example '
            '"Saccharification,66,60"'.format(raw)
        )
    name, target_text, minutes_text = parts
    try:
        target = float(target_text)
    except ValueError:
        raise InvalidInput(
            'invalid --step "{}": target "{}" must be a number of degrees C '
            "between {} and {} (e.g. 66 for saccharification)".format(
                raw, target_text, fmt(STEP_TARGET_RANGE[0], 0), fmt(STEP_TARGET_RANGE[1], 0)
            )
        ) from None
    if not math.isfinite(target) or not STEP_TARGET_RANGE[0] <= target <= STEP_TARGET_RANGE[1]:
        raise InvalidInput(
            'invalid --step "{}": target temperature {} must be between {} and '
            "{} C".format(
                raw, target_text, fmt(STEP_TARGET_RANGE[0], 0), fmt(STEP_TARGET_RANGE[1], 0)
            )
        )
    try:
        minutes = float(minutes_text)
    except ValueError:
        raise InvalidInput(
            'invalid --step "{}": minutes "{}" must be a number greater than 0 '
            "and no more than {} (e.g. 60)".format(
                raw, minutes_text, fmt(STEP_MINUTES_RANGE[1], 0)
            )
        ) from None
    if not math.isfinite(minutes) or not STEP_MINUTES_RANGE[0] < minutes <= STEP_MINUTES_RANGE[1]:
        raise InvalidInput(
            'invalid --step "{}": hold time {} must be more than 0 and at most '
            "{} minutes".format(raw, minutes_text, fmt(STEP_MINUTES_RANGE[1], 0))
        )
    return MashStep(name=name, target_temp_c=target, hold_minutes=minutes)


def parse_hop(raw: str) -> HopAddition:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4 or not parts[0]:
        raise InvalidInput(
            'invalid --hop "{}": expected "variety,alpha%,grams,minutes", for '
            'example "Cascade,5.5,30,60"'.format(raw)
        )
    variety, alpha_text, grams_text, minutes_text = parts
    try:
        alpha = float(alpha_text)
    except ValueError:
        alpha = float("nan")
    if not math.isfinite(alpha) or not ALPHA_RANGE[0] <= alpha <= ALPHA_RANGE[1]:
        raise InvalidInput(
            'invalid --hop "{}": alpha acid "{}" must be between {} and {} % '
            "(read it off the packet)".format(
                raw, alpha_text, fmt(ALPHA_RANGE[0], 0), fmt(ALPHA_RANGE[1], 0)
            )
        )
    try:
        grams = float(grams_text)
    except ValueError:
        grams = float("nan")
    if not math.isfinite(grams) or not HOP_WEIGHT_RANGE[0] < grams <= HOP_WEIGHT_RANGE[1]:
        raise InvalidInput(
            'invalid --hop "{}": weight "{}" must be a number of grams greater '
            "than {} and no more than {}".format(
                raw, grams_text, fmt(HOP_WEIGHT_RANGE[0], 0), fmt(HOP_WEIGHT_RANGE[1], 0)
            )
        )
    try:
        minutes = float(minutes_text)
    except ValueError:
        minutes = float("nan")
    if not math.isfinite(minutes) or not HOP_MINUTES_RANGE[0] <= minutes <= HOP_MINUTES_RANGE[1]:
        raise InvalidInput(
            'invalid --hop "{}": boil time "{}" must be between {} and {} '
            "minutes".format(
                raw, minutes_text, fmt(HOP_MINUTES_RANGE[0], 0), fmt(HOP_MINUTES_RANGE[1], 0)
            )
        )
    return HopAddition(variety=variety, alpha_acid_pct=alpha, weight_g=grams, boil_minutes=minutes)


def parse_range_float(
    flag: str, value: Optional[float], low: float, high: float, unit: str, places: int = 2
) -> float:
    """Range-check a flag value, naming the flag and the accepted range."""
    if value is None:
        raise InvalidInput(
            "missing {}: it is required here and must be between {} and {} {}".format(
                flag, fmt(low, places), fmt(high, places), unit
            )
        )
    if not math.isfinite(value) or not low <= value <= high:
        raise InvalidInput(
            "invalid {} {}: must be between {} and {} {}".format(
                flag, fmt(value, places), fmt(low, places), fmt(high, places), unit
            )
        )
    return value


# --------------------------------------------------------------------------
# Rendering - bill
# --------------------------------------------------------------------------
def render_swatch(rgb: Tuple[int, int, int], width: int = 40, truecolour: bool = True) -> str:
    """A full-width band of the computed beer colour (background fill)."""
    width = max(8, width)
    if truecolour:
        escape = "\x1b[48;2;{};{};{}m".format(*rgb)
    else:
        escape = "\x1b[48;5;{}m".format(rgb_to_ansi256(rgb))
    return "{}{}{}".format(escape, " " * width, "\x1b[0m")


def rgb_to_ansi256(rgb: Tuple[int, int, int]) -> int:
    """Nearest colour in the xterm-256 palette (cube 16-231, greys 232-255)."""
    r, g, b = rgb
    best = 16
    best_distance = None
    for index in range(16, 256):
        if index < 232:
            n = index - 16
            levels = (n // 36, (n // 6) % 6, n % 6)
            candidate = tuple(0 if lv == 0 else 55 + 40 * lv for lv in levels)
        else:
            shade = 8 + 10 * (index - 232)
            candidate = (shade, shade, shade)
        distance = sum((candidate[i] - (r, g, b)[i]) ** 2 for i in range(3))
        if best_distance is None or distance < best_distance:
            best, best_distance = index, distance
    return best


def accent_prefix(enabled: bool, truecolour: bool) -> Tuple[str, str]:
    """The primary-result marker: always ">", tinted with the theme accent.

    The marker text carries the meaning on its own, so colour is never the
    only signal (and it is dropped entirely when colour is unavailable).
    """
    marker = THEME["marker"]
    if not enabled:
        return marker, ""
    hex_value = THEME["accent_hex"]
    rgb = (
        int(hex_value[1:3], 16),
        int(hex_value[3:5], 16),
        int(hex_value[5:7], 16),
    )
    if truecolour:
        return marker, "\x1b[38;2;{};{};{}m".format(*rgb)
    return marker, "\x1b[38;5;{}m".format(rgb_to_ansi256(rgb))


def _swatch_width() -> int:
    # shutil pulls in fnmatch/re, so it is imported here rather than at module
    # scope: a bill or IBU run never needs the terminal width.
    import shutil

    try:
        columns = shutil.get_terminal_size((80, 24)).columns
    except OSError:  # pragma: no cover - defensive
        columns = 80
    return max(20, min(columns - 2, 72))


def render_bill(bill: MaltBill, use_colour: bool, truecolour: bool) -> str:
    """The bill view: swatch, one row per malt, then the totals block."""
    fermentables = bill_shares(bill)
    lines: List[str] = []
    width = _swatch_width()

    if use_colour:
        lines.append(render_swatch(srm_to_rgb(bill.srm), width, truecolour))
        lines.append("")

    name_width = max([len("Fermentable")] + [len(f.name) for f in fermentables])
    header = "  {:<{nw}}  {:>7}  {:>9}  {:>7}".format(
        "Fermentable", "kg", "lovibond", "% bill", nw=name_width
    )
    lines.append("Malt bill - {:.1f} L batch".format(bill.batch_volume_l))
    lines.append(header)
    lines.append(table_rule(len(header)))
    for f in fermentables:
        lines.append(
            "  {:<{nw}}  {:>7}  {:>9}  {:>6}%".format(
                f.name, fmt(f.weight_kg, 3), fmt(f.lovibond, 1), fmt(f.share_pct, 1), nw=name_width
            )
        )
    lines.append(table_rule(len(header)))
    lines.append(
        "  {:<{nw}}  {:>7}".format("Total grist", fmt(bill.total_grist_kg, 2), nw=name_width)
    )
    lines.append("")
    marker, accent = accent_prefix(use_colour, truecolour)
    reset = "\x1b[0m" if accent else ""
    lines.append(
        "{}{} SRM {:>5}   EBC {:>5}   {}{}".format(
            accent, marker, fmt(bill.srm, 1), fmt(bill.ebc, 1), bill.colour_word, reset
        )
    )
    if not use_colour:
        lines.append("  colour swatch unavailable (no colour terminal) - sRGB {}".format(
            bill.srgb_hex
        ))
    else:
        lines.append("  sRGB {}".format(bill.srgb_hex))
    return "\n".join(lines)


def bill_json(bill: MaltBill) -> dict:
    fermentables = bill_shares(bill)
    return {
        "batch_volume_l": round(bill.batch_volume_l, 3),
        "total_grist_kg": round(bill.total_grist_kg, 3),
        "fermentables": [
            {
                "name": f.name,
                "weight_kg": round(f.weight_kg, 3),
                "lovibond": round(f.lovibond, 1),
                "share_pct": round(f.share_pct, 1),
            }
            for f in fermentables
        ],
        "mcu": round(bill.mcu, 1),
        "srm": round(bill.srm, 1),
        "ebc": round(bill.ebc, 1),
        "colour_word": bill.colour_word,
        "srgb_hex": bill.srgb_hex,
    }


# --------------------------------------------------------------------------
# Rendering - mash
# --------------------------------------------------------------------------
def render_strike(lookup: StrikeLookup, use_colour: bool, truecolour: bool = True) -> str:
    """The strike view: the tabulated grid with the row used marked, then the
    interpolated number and the grain correction that produced it."""
    mark, accent = accent_prefix(use_colour, truecolour)
    reset = "\x1b[0m" if accent else ""
    head = "  ratio L/kg | " + "  ".join("{:>5}".format(fmt(t, 0)) for t in STRIKE_TARGETS)
    lines = [
        "Strike water temperature  (grain {} C, ratio {} L/kg, target {} C)".format(
            fmt(lookup.grain_temp_c, 1),
            fmt(lookup.ratio_l_per_kg, 2),
            fmt(lookup.target_temp_c, 1),
        ),
        head + "   C",
        "  " + table_rule(len(head) - 2),
    ]
    for _, row_ratio, cells in STRIKE_TABLE:
        row = "{:>10} | ".format(fmt(row_ratio, 1)) + "  ".join(
            "{:>5}".format(fmt(c, 1)) for c in cells
        )
        if row_ratio == lookup.row_ratio:
            lines.append("{}{} {}{}".format(accent, mark, row, reset))
        else:
            lines.append("  " + row)
    lines.append("")
    lines.append(
        "  selected row {} L/kg x {} C -> {} C  ({} between table cells)".format(
            fmt(lookup.row_ratio, 1),
            fmt(lookup.target_temp_c, 1),
            fmt(lookup.strike_temp_c, 1),
            lookup.row_source,
        )
    )
    lines.append(
        "  grain correction: grain at {} C vs the {} C table reference, {:+.1f} C "
        "applied".format(
            fmt(lookup.grain_temp_c, 1), fmt(GRAIN_TEMP_REFERENCE, 0), lookup.grain_correction_c
        )
    )
    lines.append(
        "{} strike water: {} C{}".format(mark, fmt(lookup.strike_temp_c, 1), reset)
    )
    return "\n".join(lines)


def strike_json(lookup: StrikeLookup) -> dict:
    return {
        "ratio_l_per_kg": round(lookup.ratio_l_per_kg, 2),
        "target_temp_c": round(lookup.target_temp_c, 1),
        "grain_temp_c": round(lookup.grain_temp_c, 1),
        "strike_temp_c": round(lookup.strike_temp_c, 1),
        "row_ratio_l_per_kg": round(lookup.row_ratio, 1),
        "source": lookup.row_source,
        "grain_correction_c": round(lookup.grain_correction_c, 2),
        "table": {
            fmt(row_ratio, 1): {
                fmt(t, 0): cells[i] for i, t in enumerate(STRIKE_TARGETS)
            }
            for _, row_ratio, cells in STRIKE_TABLE
        },
    }


def render_schedule(schedule: MashSchedule) -> str:
    name_width = max([len("Step")] + [len(s.name) for s in schedule.steps])
    header = "  {:<{nw}}  {:>8}  {:>8}  {:>11}  {:>13}".format(
        "Step", "target", "hold", "strike", "clock (mm:ss)", nw=name_width
    )
    lines = [
        "Mash schedule - {} L/kg, grain {} C".format(
            fmt(schedule.ratio_l_per_kg, 2), fmt(schedule.grain_temp_c, 1)
        ),
        header,
        table_rule(len(header)),
    ]
    for index, step in enumerate(schedule.steps, start=1):
        lines.append(
            "  {:<{nw}}  {:>6} C  {:>5} m  {:>8} C  {:>7}-{:<5}".format(
                "{}. {}".format(index, step.name),
                fmt(step.target_temp_c, 1),
                fmt(step.hold_minutes, 2),
                fmt(step.strike_temp_c, 1),
                mmss(step.start_offset_min * 60),
                mmss(step.end_offset_min * 60),
                nw=name_width + 3,
            )
        )
    lines.append(table_rule(len(header)))
    lines.append(
        "  total mash time {} min across {} step{}".format(
            fmt(schedule.total_minutes, 2),
            len(schedule.steps),
            "" if len(schedule.steps) == 1 else "s",
        )
    )
    return "\n".join(lines)


def schedule_json(schedule: MashSchedule) -> dict:
    return {
        "ratio_l_per_kg": round(schedule.ratio_l_per_kg, 2),
        "grain_temp_c": round(schedule.grain_temp_c, 1),
        "total_minutes": round(schedule.total_minutes, 2),
        "steps": [
            {
                "name": s.name,
                "target_temp_c": round(s.target_temp_c, 1),
                "hold_minutes": round(s.hold_minutes, 2),
                "strike_temp_c": round(s.strike_temp_c, 1),
                "start_offset_min": round(s.start_offset_min, 2),
            }
            for s in schedule.steps
        ],
    }


# --------------------------------------------------------------------------
# Rendering - bitterness
# --------------------------------------------------------------------------
def render_ibu(total: IbuTotal) -> str:
    shares = ibu_shares(total)
    variety_width = max([len("Hop")] + [len(a.variety) for a in total.additions])
    header = "  {:<{vw}}  {:>7}  {:>7}  {:>8}  {:>6}  {:>7}  {:>7}".format(
        "Hop", "alpha", "grams", "minutes", "util", "IBU", "% of IBU", vw=variety_width
    )
    lines = [
        "Hop bitterness (Tinseth) - {} L at OG {}".format(
            fmt(total.batch_volume_l, 1), fmt(total.boil_gravity, 3)
        ),
        header,
        table_rule(len(header)),
    ]
    for add, share in zip(total.additions, shares):
        lines.append(
            "  {:<{vw}}  {:>5} %  {:>5} g  {:>6} m  {:>5} %  {:>7}  {:>6}%".format(
                add.variety,
                fmt(add.alpha_acid_pct, 1),
                fmt(add.weight_g, 1),
                fmt(add.boil_minutes, 0),
                fmt(add.utilization_pct, 1),
                fmt(add.ibu, 1),
                fmt(share, 1),
                vw=variety_width,
            )
        )
    lines.append(table_rule(len(header)))
    lines.append("> total kettle IBU {}".format(fmt(total.total_ibu, 1)))
    lines.append(
        "  shares are each charge's percentage of that total; Tinseth is one "
        "model, not a lab measurement."
    )
    if total.short_boil:
        lines.append(
            "  note: Tinseth under-estimates very late additions - a charge boiled "
            "under 20 min reads lower here than it tastes."
        )
    return "\n".join(lines)


def ibu_json(total: IbuTotal) -> dict:
    shares = ibu_shares(total)
    return {
        "batch_volume_l": round(total.batch_volume_l, 2),
        "boil_gravity": round(total.boil_gravity, 3),
        "total_ibu": round(total.total_ibu, 1),
        "short_boil_note": total.short_boil,
        "additions": [
            {
                "variety": a.variety,
                "alpha_acid_pct": round(a.alpha_acid_pct, 1),
                "weight_g": round(a.weight_g, 1),
                "boil_minutes": round(a.boil_minutes, 1),
                "utilization_pct": round(a.utilization_pct, 1),
                "ibu": round(a.ibu, 1),
                "share_pct": round(share, 1),
            }
            for a, share in zip(total.additions, shares)
        ],
    }


# --------------------------------------------------------------------------
# The live countdown
# --------------------------------------------------------------------------
BELL = "\a"
TICK_SECONDS = 0.1
KEY_STOP = "q"


class _RawTerminal:
    """Put stdin into cbreak mode so a single keypress stops the timer."""

    def __init__(self, stream: object = None) -> None:
        self.stream = stream if stream is not None else sys.stdin
        self._saved = None

    def __enter__(self) -> "_RawTerminal":
        try:
            import termios
            import tty
        except ImportError:  # pragma: no cover - non-POSIX
            return self
        isatty = getattr(self.stream, "isatty", None)
        try:
            if callable(isatty) and isatty():
                self._saved = termios.tcgetattr(self.stream)
                tty.setcbreak(self.stream.fileno())
        except (OSError, ValueError, AttributeError):  # pragma: no cover - defensive
            self._saved = None
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._saved is None:
            return
        try:
            import termios

            termios.tcsetattr(self.stream, termios.TCSADRAIN, self._saved)
        except (OSError, ValueError, ImportError):  # pragma: no cover - defensive
            pass


def read_stop_key(stream: object = None) -> str:
    """Return the next pending character, or '' when nothing was typed."""
    stream = stream if stream is not None else sys.stdin
    try:
        import select
    except ImportError:  # pragma: no cover - non-POSIX
        return ""
    try:
        ready, _, _ = select.select([stream], [], [], 0)
    except (OSError, ValueError, TypeError):  # pragma: no cover - defensive
        return ""
    if not ready:
        return ""
    try:
        chunk = stream.read(1)
    except (OSError, ValueError, AttributeError):  # pragma: no cover - defensive
        return ""
    return chunk or ""


def _countdown_line(step: MashStep, index: int, count: int, remaining: float) -> str:
    return "\r  step {}/{}  {:<24} target {:>5} C  remaining {}   ".format(
        index, count, step.name[:24], fmt(step.target_temp_c, 1), mmss(remaining)
    )


def run_countdown(
    schedule: MashSchedule,
    out: object,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    read_key: Optional[Callable[[], str]] = None,
    interactive: bool = True,
) -> str:
    """Block the terminal and count the schedule down, one line, one bell a step.

    Returns "completed" when the mash ran out, or "stopped" when the brewer
    pressed q.  Ctrl-C is re-raised after the line and terminal are restored.
    """
    read_key = read_stop_key if read_key is None else read_key
    count = len(schedule.steps)
    finished = False
    stopped = False
    try:
        for index, step in enumerate(schedule.steps, start=1):
            step_seconds = step.hold_minutes * 60.0
            start = clock()
            shown = None
            while True:
                remaining = step_seconds - (clock() - start)
                if remaining <= 0:
                    break
                text = _countdown_line(step, index, count, remaining)
                if text != shown:
                    out.write(text)
                    out.flush()
                    shown = text
                if interactive and read_key().lower() == KEY_STOP:
                    stopped = True
                    break
                sleep(min(TICK_SECONDS, remaining))
            if stopped:
                break
            out.write("\r" + " " * 78 + "\r")
            out.write(
                "  step {}/{}  {:<24} target {:>5} C  done\n".format(
                    index, count, step.name[:24], fmt(step.target_temp_c, 1)
                )
            )
            # A bell at every step boundary; the closing bell of the last step
            # is the one sounded by the completion line below.
            if index < count:
                out.write(BELL)
            out.flush()
        finished = not stopped
        if finished:
            out.write(BELL)
            out.write(
                "  mash complete - {} step{}, {} min\n".format(
                    count, "" if count == 1 else "s", fmt(schedule.total_minutes, 2)
                )
            )
        else:
            out.write("\n")
            out.write("  stopped on step {}/{}; the mash clock is not running\n".format(index, count))
        out.flush()
        return "completed" if finished else "stopped"
    except KeyboardInterrupt:
        out.write("\n")
        out.flush()
        raise


# --------------------------------------------------------------------------
# Empty states
# --------------------------------------------------------------------------
EMPTY_BILL_HINT = (
    "No malts yet. Add your first fermentable, for example:\n"
    '  {prog} bill --add "Pilsner,5,1.8" --volume 23'
)
EMPTY_STRIKE_HINT = (
    "This is the default table for a 3.0 L/kg ratio and a 66 C target - pick "
    "your own, for example:\n"
    "  {prog} mash strike --ratio 3.0 --target 66 --grain-temp 12"
)
EMPTY_TIMER_HINT = (
    "No mash steps yet. Add one, for example:\n"
    '  {prog} mash timer --step "Saccharification,66,60" --plan-only'
)
EMPTY_IBU_HINT = (
    "No hops yet. Add your first charge, for example:\n"
    '  {prog} ibu --hop "Cascade,5.5,30,60" --volume 23 --gravity 1.050'
)


# --------------------------------------------------------------------------
# Command handlers
# --------------------------------------------------------------------------
def _write(text: str, stream: object = None) -> None:
    stream = sys.stdout if stream is None else stream
    stream.write(text + "\n")


def _emit(payload: dict, as_json: bool, text: str, stream: object = None) -> None:
    if as_json:
        import json  # only the --json path pays for it

        _write(json.dumps(payload, indent=2, sort_keys=False), stream)
    else:
        _write(text, stream)


def cmd_bill(args: argparse.Namespace) -> int:
    out = sys.stdout
    as_json = bool(getattr(args, "json", False))
    adds = list(args.add or [])
    if not adds:
        _write(EMPTY_BILL_HINT.format(prog=PROG), out)
        return EXIT_OK
    fermentables = []
    for raw in adds:
        name, weight, lovibond = parse_fermentable(raw)
        fermentables.append(Fermentable(name, weight, lovibond))
    bill = build_bill(args.volume, fermentables)
    use_colour = (not args.no_color) and colour_enabled(out)
    is_truecolour = use_colour and truecolour_enabled(out)
    _emit(bill_json(bill), as_json, render_bill(bill, use_colour, is_truecolour), out)
    return EXIT_OK


def cmd_mash_strike(args: argparse.Namespace) -> int:
    out = sys.stdout
    as_json = bool(getattr(args, "json", False))
    bare_invocation = args.ratio is None and args.target is None
    ratio = parse_range_float(
        "--ratio", 3.0 if args.ratio is None else args.ratio,
        RATIO_RANGE[0], RATIO_RANGE[1], "L/kg", 2,
    )
    target = parse_range_float(
        "--target", 66.0 if args.target is None else args.target,
        STRIKE_TARGET_RANGE[0], STRIKE_TARGET_RANGE[1], "C", 1,
    )
    grain_temp = parse_range_float(
        "--grain-temp", args.grain_temp, GRAIN_TEMP_RANGE[0], GRAIN_TEMP_RANGE[1], "C", 1
    )
    lookup = strike_temperature(ratio, target, grain_temp)
    if as_json:
        import json  # only the --json path pays for it

        _write(json.dumps(strike_json(lookup), indent=2))
        return EXIT_OK
    use_colour = colour_enabled(out)
    _write(render_strike(lookup, use_colour, use_colour and truecolour_enabled(out)), out)
    if bare_invocation:
        _write(EMPTY_STRIKE_HINT.format(prog=PROG), out)
    return EXIT_OK


def timer_is_interactive() -> bool:
    """True when the countdown can own the terminal (both ends are TTYs)."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def cmd_mash_timer(args: argparse.Namespace) -> int:
    out = sys.stdout
    err = sys.stderr
    as_json = bool(getattr(args, "json", False))
    raw_steps = list(args.step or [])
    if not raw_steps:
        _write(EMPTY_TIMER_HINT.format(prog=PROG), out)
        return EXIT_OK
    ratio = parse_range_float("--ratio", args.ratio, RATIO_RANGE[0], RATIO_RANGE[1], "L/kg", 2)
    grain_temp = parse_range_float(
        "--grain-temp", args.grain_temp, GRAIN_TEMP_RANGE[0], GRAIN_TEMP_RANGE[1], "C", 1
    )
    schedule = build_schedule([parse_step(raw) for raw in raw_steps], ratio, grain_temp)
    _emit(schedule_json(schedule), as_json, render_schedule(schedule), out)
    if args.plan_only:
        return EXIT_OK

    interactive = timer_is_interactive()
    if not interactive:
        _write(
            "stdout is not a terminal, so the live countdown is skipped; the "
            "schedule above is the plan (pass --plan-only to say so explicitly).",
            err,
        )
        return EXIT_OK
    _write("", out)
    stream = _TerminalWriter(out)
    with _RawTerminal():
        status = run_countdown(schedule, stream, clock=DEFAULT_CLOCK, sleep=DEFAULT_SLEEP)
    _write("  " + ("mash finished" if status == "completed" else "stopped"), out)
    return EXIT_OK


class _TerminalWriter:
    """Write straight through to a terminal stream (no buffering surprises)."""

    def __init__(self, stream: object) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        return self._stream.write(text)

    def flush(self) -> None:
        self._stream.flush()


def cmd_ibu(args: argparse.Namespace) -> int:
    out = sys.stdout
    as_json = bool(getattr(args, "json", False))
    raw_hops = list(args.hop or [])
    if not raw_hops:
        _write(EMPTY_IBU_HINT.format(prog=PROG), out)
        return EXIT_OK
    volume = parse_range_float("--volume", args.volume, VOLUME_RANGE[0], VOLUME_RANGE[1], "L", 1)
    gravity = parse_range_float(
        "--gravity", args.gravity, GRAVITY_RANGE[0], GRAVITY_RANGE[1], "SG", 3
    )
    additions = [parse_hop(raw) for raw in raw_hops]
    total = build_ibu_total(additions, volume, gravity)
    _emit(ibu_json(total), as_json, render_ibu(total), out)
    return EXIT_OK


# --------------------------------------------------------------------------
# The plan's in-process API contract
# --------------------------------------------------------------------------
# Grainwell opens no sockets and starts no HTTP server: the contract's
# "endpoints" are plain functions the argparse layer calls, listed here so the
# mapping from path to implementation is explicit and testable.
IN_PROCESS_CONTRACT = {
    "/api/malt-bill": build_bill,
    "/api/malt-bill/color": colour_report,
    "/api/mash/strike-table": strike_temperature,
    "/api/mash/schedule": build_schedule,
    "/api/mash/timer": run_countdown,
    "/api/hops/ibu": ibu_for_addition,
    "/api/hops/ibu/total": build_ibu_total,
}


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
HELP_EXAMPLES = """examples:
  {p} bill --add "Pilsner,5,1.8" --add "Crystal 60,0.5,60" --volume 23
  {p} mash strike --ratio 3.0 --target 66 --grain-temp 20
  {p} mash timer --step "Saccharification,66,60" --step "Mash out,76,10" --plan-only
  {p} ibu --hop "Cascade,5.5,30,60" --hop "Saaz,3.5,20,15" --volume 23 --gravity 1.050

exit codes: 0 success, 2 invalid input, 130 interrupted during the countdown
output: results on stdout, diagnostics on stderr; NO_COLOR and non-TTY disable
the colour swatch. Nothing is written to disk and nothing is networked."""


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true", help="print the result as JSON for scripting"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Malt bill colour, mash strike temperatures and Tinseth IBU for "
            "all-grain brewing, in metric. Nothing is stored and nothing is "
            "networked."
        ),
        epilog=HELP_EXAMPLES.format(p=PROG),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="{} {}".format(PROG, __version__))
    subparsers = parser.add_subparsers(dest="command", metavar="command", required=True)

    bill = subparsers.add_parser(
        "bill",
        help="build a malt bill and preview the beer colour",
        description="Build a grist from typed fermentables and report the beer colour.",
        epilog='example: {p} bill --add "Pilsner,5,1.8" --add "Crystal 60,0.5,60" --volume 23'.format(p=PROG),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    bill.add_argument(
        "--add",
        action="append",
        metavar='"name,kg,lovibond"',
        help='repeatable fermentable, e.g. "Pilsner,5,1.8" (kg > 0, lovibond 0-600)',
    )
    bill.add_argument(
        "--volume", type=float, default=None, metavar="L",
        help="batch volume in litres (%s-%s L)" % (fmt(VOLUME_RANGE[0], 1), fmt(VOLUME_RANGE[1], 0)),
    )
    bill.add_argument("--no-color", "--no-colour", action="store_true", dest="no_color",
                      help="never emit ANSI colour (NO_COLOR is honoured too)")
    _add_common(bill)
    bill.set_defaults(handler=cmd_bill)

    mash = subparsers.add_parser(
        "mash",
        help="strike temperature table and the mash step timer",
        description="Strike water lookup and the mash schedule timer.",
    )
    mash_sub = mash.add_subparsers(dest="mash_command", metavar="subcommand", required=True)

    strike = mash_sub.add_parser(
        "strike",
        help="print the strike-temperature table and the row to use",
        description="Print the strike-temperature grid and the interpolated strike water.",
        epilog="example: {p} mash strike --ratio 3.0 --target 66 --grain-temp 20".format(p=PROG),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    strike.add_argument("--ratio", type=float, default=None, metavar="L/kg",
                        help="water:grain ratio (%s-%s L/kg, default 3.0)"
                             % (fmt(RATIO_RANGE[0], 1), fmt(RATIO_RANGE[1], 1)))
    strike.add_argument("--target", type=float, default=None, metavar="C",
                        help="target mash temperature (%s-%s C, default 66)"
                             % (fmt(STRIKE_TARGET_RANGE[0], 0), fmt(STRIKE_TARGET_RANGE[1], 0)))
    strike.add_argument("--grain-temp", type=float, default=GRAIN_TEMP_REFERENCE, metavar="C",
                        help="grain bed temperature (%s-%s C, default 20)"
                             % (fmt(GRAIN_TEMP_RANGE[0], 0), fmt(GRAIN_TEMP_RANGE[1], 0)))
    _add_common(strike)
    strike.set_defaults(handler=cmd_mash_strike)

    timer = mash_sub.add_parser(
        "timer",
        help="build a mash schedule and (optionally) run the live countdown",
        description="Order the mash steps, print the plan, then count it down.",
        epilog='example: {p} mash timer --step "Saccharification,66,60" --plan-only'.format(p=PROG),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    timer.add_argument(
        "--step", action="append", metavar='"name,target_C,minutes"',
        help='repeatable step, e.g. "Saccharification,66,60" '
             "(target %s-%s C, minutes > 0 and <= %s)"
             % (fmt(STEP_TARGET_RANGE[0], 0), fmt(STEP_TARGET_RANGE[1], 0),
                fmt(STEP_MINUTES_RANGE[1], 0)),
    )
    timer.add_argument("--ratio", type=float, default=3.0, metavar="L/kg",
                       help="water:grain ratio (%s-%s L/kg, default 3.0)"
                            % (fmt(RATIO_RANGE[0], 1), fmt(RATIO_RANGE[1], 1)))
    timer.add_argument("--grain-temp", type=float, default=GRAIN_TEMP_REFERENCE, metavar="C",
                       help="grain bed temperature (%s-%s C, default 20)"
                            % (fmt(GRAIN_TEMP_RANGE[0], 0), fmt(GRAIN_TEMP_RANGE[1], 0)))
    timer.add_argument("--plan-only", action="store_true",
                       help="print the schedule and exit without counting down")
    _add_common(timer)
    timer.set_defaults(handler=cmd_mash_timer)

    ibu = subparsers.add_parser(
        "ibu",
        help="Tinseth bitterness from typed alpha acids",
        description="Compute Tinseth utilization and IBU from alpha acids you type.",
        epilog='example: {p} ibu --hop "Cascade,5.5,30,60" --volume 23 --gravity 1.050'.format(p=PROG),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ibu.add_argument(
        "--hop", action="append", metavar='"variety,alpha%,grams,minutes"',
        help='repeatable hop charge, e.g. "Cascade,5.5,30,60" '
             "(alpha acid %s-%s percent, grams > 0, minutes %s-%s)"
             % (fmt(ALPHA_RANGE[0], 0), fmt(ALPHA_RANGE[1], 0),
                fmt(HOP_MINUTES_RANGE[0], 0), fmt(HOP_MINUTES_RANGE[1], 0)),
    )
    ibu.add_argument("--volume", type=float, default=None, metavar="L",
                     help="batch volume in litres (%s-%s L)"
                          % (fmt(VOLUME_RANGE[0], 1), fmt(VOLUME_RANGE[1], 0)))
    ibu.add_argument("--gravity", type=float, default=None, metavar="SG",
                     help="boil gravity (%s-%s, e.g. 1.050)"
                          % (fmt(GRAVITY_RANGE[0], 3), fmt(GRAVITY_RANGE[1], 3)))
    _add_common(ibu)
    ibu.set_defaults(handler=cmd_ibu)

    return parser


# The clock and sleep used by the live countdown; named here so the schedule
# logic can be exercised in tests without waiting for real minutes.
DEFAULT_CLOCK: Callable[[], float] = time.monotonic
DEFAULT_SLEEP: Callable[[float], None] = time.sleep


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point: parse arguments, run the command, return an exit code."""
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:  # argparse already printed usage/help
        code = exc.code
        return int(code) if isinstance(code, int) else EXIT_INVALID

    handler = getattr(args, "handler", None)
    if handler is None:  # pragma: no cover - subparsers are required
        parser.print_usage(sys.stderr)
        return EXIT_INVALID
    try:
        return int(handler(args))
    except InvalidInput as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return EXIT_INVALID
    except KeyboardInterrupt:
        print("\ninterrupted - mash timer stopped, terminal left usable", file=sys.stderr)
        return EXIT_INTERRUPTED
    except BrokenPipeError:  # pragma: no cover - `grainwell ... | head`
        try:
            sys.stdout.close()
        finally:
            return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
