from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from statistics import fmean
from typing import Iterable


MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


@dataclass
class OptionRow:
    expiry_code: str
    expiry_iso: str
    strike: int
    side: str
    bid: float
    ask: float
    underlying_price: float


def parse_expiry_code(expiry_code: str) -> datetime:
    match = re.fullmatch(r"(\d{1,2})([A-Z]{3})(\d{2})", expiry_code)
    if not match:
        raise ValueError(f"Unsupported expiry code: {expiry_code}")
    day, month_code, year_code = match.groups()
    month = MONTHS[month_code]
    year = 2000 + int(year_code)
    return datetime(year, month, int(day), tzinfo=timezone.utc)


def list_expiries_from_api(api_rows: list[dict]) -> list[dict]:
    expiries = {}
    for option in api_rows:
        expiry_code = option["instrument_name"].split("-")[1]
        if expiry_code not in expiries:
            expiry_dt = parse_expiry_code(expiry_code)
            expiries[expiry_code] = {
                "code": expiry_code,
                "iso": expiry_dt.date().isoformat(),
                "label": expiry_dt.strftime("%d %b %Y"),
            }
    return [expiries[key] for key in sorted(expiries, key=lambda item: parse_expiry_code(item))]


def parse_option_rows(api_rows: list[dict]) -> list[OptionRow]:
    parsed: list[OptionRow] = []
    for option in api_rows:
        currency, expiry_code, strike, side = option["instrument_name"].split("-")
        if currency != "BTC":
            continue
        expiry_iso = parse_expiry_code(expiry_code).date().isoformat()
        parsed.append(
            OptionRow(
                expiry_code=expiry_code,
                expiry_iso=expiry_iso,
                strike=int(strike),
                side=side,
                bid=float(option.get("bid_price") or 0.0),
                ask=float(option.get("ask_price") or 0.0),
                underlying_price=float(option.get("underlying_price") or 0.0),
            )
        )
    return parsed


def list_expiries_from_json(raw_json: str) -> list[dict]:
    return list_expiries_from_api(json.loads(raw_json))


def _moving_average(values: list[float], window: int) -> list[float]:
    if window <= 1:
        return list(values)

    radius = max(window // 2, 1)
    smoothed: list[float] = []
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        sample = values[start:end]
        smoothed.append(sum(sample) / len(sample))
    return smoothed


def _normalize(values: list[float]) -> list[float]:
    total = sum(values)
    if total <= 0:
        return values
    return [value / total for value in values]


def _quantile(points: list[dict], probability: float) -> float:
    running = 0.0
    for point in points:
        running += point["probability"]
        if running >= probability:
            return point["strike"]
    return points[-1]["strike"]


def _select_existing(rows: list[OptionRow], strike: int) -> OptionRow | None:
    for row in rows:
        if row.strike == strike:
            return row
    return None


def _interpolate_row(left: OptionRow, right: OptionRow, strike: int, mean_underlying: float) -> dict:
    weight = (strike - left.strike) / (right.strike - left.strike)
    return {
        "strike": strike,
        "bid": left.bid + ((right.bid - left.bid) * weight),
        "ask": left.ask + ((right.ask - left.ask) * weight),
        "underlying_price": mean_underlying,
        "real": False,
    }


def _rebuild_side_grid(rows: Iterable[OptionRow]) -> dict:
    sorted_rows = sorted(rows, key=lambda row: row.strike)
    if len(sorted_rows) < 3:
        raise ValueError("Not enough option rows to estimate a distribution.")

    strikes = [row.strike for row in sorted_rows]
    diffs = [right - left for left, right in zip(strikes, strikes[1:]) if right > left]
    if not diffs:
        raise ValueError("Could not infer strike spacing from the option chain.")

    step = min(diffs)
    start = strikes[0]
    end = strikes[-1]
    mean_underlying = fmean([row.underlying_price for row in sorted_rows if row.underlying_price > 0])

    full_rows: list[dict] = []
    for strike in range(start, end + step, step):
        existing = _select_existing(sorted_rows, strike)
        if existing:
            full_rows.append(
                {
                    "strike": existing.strike,
                    "bid": existing.bid,
                    "ask": existing.ask,
                    "underlying_price": mean_underlying,
                    "real": True,
                }
            )
            continue

        left = max((row for row in sorted_rows if row.strike < strike), key=lambda row: row.strike)
        right = min((row for row in sorted_rows if row.strike > strike), key=lambda row: row.strike)
        full_rows.append(_interpolate_row(left, right, strike, mean_underlying))

    return {
        "step": step,
        "underlying_price": mean_underlying,
        "rows": full_rows,
        "real_count": sum(1 for row in full_rows if row["real"]),
        "interpolated_count": sum(1 for row in full_rows if not row["real"]),
    }


def _butterfly_surface(grid: dict) -> list[dict]:
    rows = grid["rows"]
    step = grid["step"]
    points: list[dict] = []
    for index in range(1, len(rows) - 1):
        previous_row = rows[index - 1]
        current_row = rows[index]
        next_row = rows[index + 1]

        previous_mid = (previous_row["bid"] + previous_row["ask"]) / 2
        current_mid = (current_row["bid"] + current_row["ask"]) / 2
        next_mid = (next_row["bid"] + next_row["ask"]) / 2

        center = (previous_mid - (2 * current_mid) + next_mid) * current_row["underlying_price"] / step
        long_execution = (
            previous_row["ask"] - (2 * current_row["bid"]) + next_row["ask"]
        ) * current_row["underlying_price"] / step
        short_execution = (
            (-previous_row["bid"]) + (2 * current_row["ask"]) - next_row["bid"]
        ) * current_row["underlying_price"] / step

        points.append(
            {
                "strike": current_row["strike"],
                "center": center,
                "lower": min(long_execution, short_execution),
                "upper": max(long_execution, short_execution),
                "real": current_row["real"],
            }
        )
    return points


def _blend_surfaces(call_surface: list[dict], put_surface: list[dict], spot: float, source: str) -> list[dict]:
    calls = {point["strike"]: point for point in call_surface}
    puts = {point["strike"]: point for point in put_surface}
    strikes = sorted(set(calls) | set(puts))

    def merge(points: list[dict], label: str) -> dict:
        return {
            "strike": points[0]["strike"],
            "center": sum(point["center"] for point in points) / len(points),
            "lower": sum(point["lower"] for point in points) / len(points),
            "upper": sum(point["upper"] for point in points) / len(points),
            "label": label,
            "real": any(point["real"] for point in points),
        }

    blended: list[dict] = []
    for strike in strikes:
        call_point = calls.get(strike)
        put_point = puts.get(strike)

        if source == "calls" and call_point:
            blended.append(merge([call_point], "calls"))
            continue
        if source == "puts" and put_point:
            blended.append(merge([put_point], "puts"))
            continue
        if source == "average":
            candidates = [point for point in (put_point, call_point) if point]
            if candidates:
                blended.append(merge(candidates, "average"))
            continue

        if strike < spot and put_point:
            blended.append(merge([put_point], "auto"))
        elif strike >= spot and call_point:
            blended.append(merge([call_point], "auto"))
        elif put_point and call_point:
            blended.append(merge([put_point, call_point], "auto-fallback"))
        elif put_point:
            blended.append(merge([put_point], "auto-put-fallback"))
        elif call_point:
            blended.append(merge([call_point], "auto-call-fallback"))

    return blended


def compute_distribution_from_api(
    api_rows: list[dict],
    expiry_code: str,
    source: str = "auto",
    smoothing_window: int = 3,
    clip_negative: bool = True,
) -> dict:
    rows = parse_option_rows(api_rows)
    scoped_rows = [row for row in rows if row.expiry_code == expiry_code]
    if not scoped_rows:
        raise ValueError(f"Expiry {expiry_code} was not found in the option chain.")

    call_rows = [row for row in scoped_rows if row.side == "C"]
    put_rows = [row for row in scoped_rows if row.side == "P"]
    if len(call_rows) < 3 or len(put_rows) < 3:
        raise ValueError("This expiry does not have enough call and put quotes.")

    call_grid = _rebuild_side_grid(call_rows)
    put_grid = _rebuild_side_grid(put_rows)
    spot = fmean([call_grid["underlying_price"], put_grid["underlying_price"]])

    call_surface = _butterfly_surface(call_grid)
    put_surface = _butterfly_surface(put_grid)
    blended_surface = _blend_surfaces(call_surface, put_surface, spot=spot, source=source)

    centers = [point["center"] for point in blended_surface]
    lowers = [point["lower"] for point in blended_surface]
    uppers = [point["upper"] for point in blended_surface]

    if clip_negative:
        centers = [max(value, 0.0) for value in centers]
        lowers = [max(value, 0.0) for value in lowers]
        uppers = [max(value, 0.0) for value in uppers]

    centers = _moving_average(centers, smoothing_window)
    lowers = _moving_average(lowers, smoothing_window)
    uppers = _moving_average(uppers, smoothing_window)

    probabilities = _normalize(centers)
    lower_probabilities = _normalize(lowers)
    upper_probabilities = _normalize(uppers)
    if sum(probabilities) <= 0:
        raise ValueError("Could not normalize the implied distribution.")

    points: list[dict] = []
    for index, surface_point in enumerate(blended_surface):
        points.append(
            {
                "strike": surface_point["strike"],
                "probability": probabilities[index],
                "lower_probability": lower_probabilities[index],
                "upper_probability": upper_probabilities[index],
                "real": surface_point["real"],
                "source": surface_point["label"],
            }
        )

    expiry_dt = parse_expiry_code(expiry_code)
    now = datetime.now(timezone.utc)
    days_to_expiry = max((expiry_dt.date() - now.date()).days, 0)

    mean_price = sum(point["strike"] * point["probability"] for point in points)
    mode_point = max(points, key=lambda point: point["probability"])
    median_price = _quantile(points, 0.5)
    band_10 = _quantile(points, 0.10)
    band_90 = _quantile(points, 0.90)
    annualized_return = None
    if spot > 0 and days_to_expiry > 0 and mean_price > 0:
        annualized_return = ((mean_price / spot) ** (365 / days_to_expiry)) - 1

    return {
        "expiry": {
            "code": expiry_code,
            "iso": expiry_dt.date().isoformat(),
            "label": expiry_dt.strftime("%d %b %Y"),
            "days_to_expiry": days_to_expiry,
        },
        "spot_price": spot,
        "mean_price": mean_price,
        "median_price": median_price,
        "mode_price": mode_point["strike"],
        "probability_above_spot": sum(point["probability"] for point in points if point["strike"] > spot),
        "annualized_return": annualized_return,
        "band_10": band_10,
        "band_25": _quantile(points, 0.25),
        "band_75": _quantile(points, 0.75),
        "band_90": band_90,
        "quality": {
            "call_real_strikes": call_grid["real_count"],
            "call_interpolated_strikes": call_grid["interpolated_count"],
            "put_real_strikes": put_grid["real_count"],
            "put_interpolated_strikes": put_grid["interpolated_count"],
            "call_step": call_grid["step"],
            "put_step": put_grid["step"],
        },
        "points": points,
    }


def compute_distribution_from_json(
    raw_json: str,
    expiry_code: str,
    source: str = "auto",
    smoothing_window: int = 3,
    clip_negative: bool = True,
) -> dict:
    return compute_distribution_from_api(
        json.loads(raw_json),
        expiry_code=expiry_code,
        source=source,
        smoothing_window=smoothing_window,
        clip_negative=clip_negative,
    )
