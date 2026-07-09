from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py"))

from implied_distribution import compute_distribution_from_api, list_expiries_from_api, parse_expiry_code


def build_option(instrument_name: str, mid_price: float, underlying_price: float = 100.0) -> dict:
    return {
        "instrument_name": instrument_name,
        "bid_price": mid_price,
        "ask_price": mid_price,
        "mid_price": mid_price,
        "underlying_price": underlying_price,
    }


def synthetic_chain() -> list[dict]:
    call_prices = {
        80: 0.29,
        90: 0.17,
        100: 0.07,
        110: 0.02,
        120: 0.0,
    }
    rows = []
    for strike, mid_price in call_prices.items():
        rows.append(build_option(f"BTC-31DEC26-{strike}-C", mid_price))
        rows.append(build_option(f"BTC-31DEC26-{strike}-P", mid_price))
    return rows


def test_lists_expiries_in_order():
    expiries = list_expiries_from_api(
        [
            build_option("BTC-31DEC26-100-C", 0.1),
            build_option("BTC-10JUL26-100-C", 0.1),
        ]
    )
    assert [expiry["code"] for expiry in expiries] == ["10JUL26", "31DEC26"]


def test_supports_single_digit_expiry_codes():
    parsed = parse_expiry_code("9JUL26")
    assert parsed.date().isoformat() == "2026-07-09"


def test_recovers_probability_mass_from_known_surface():
    result = compute_distribution_from_api(
        synthetic_chain(),
        expiry_code="31DEC26",
        source="calls",
        smoothing_window=1,
        clip_negative=True,
    )

    strikes = [point["strike"] for point in result["points"]]
    probs = [round(point["probability"], 4) for point in result["points"]]

    assert strikes == [90, 100, 110]
    assert probs == [0.2, 0.5, 0.3]
    assert round(result["mean_price"], 4) == 101.0
    assert result["mode_price"] == 100
    assert round(result["probability_above_spot"], 4) == 0.3


def test_auto_blend_interpolates_missing_strikes():
    chain = synthetic_chain()
    chain = [row for row in chain if row["instrument_name"] != "BTC-31DEC26-100-C"]
    chain = [row for row in chain if row["instrument_name"] != "BTC-31DEC26-100-P"]

    result = compute_distribution_from_api(
        chain,
        expiry_code="31DEC26",
        source="auto",
        smoothing_window=1,
        clip_negative=True,
    )

    assert len(result["points"]) == 3
    assert result["quality"]["call_interpolated_strikes"] == 1
    assert result["quality"]["put_interpolated_strikes"] == 1
    assert abs(sum(point["probability"] for point in result["points"]) - 1.0) < 1e-9
