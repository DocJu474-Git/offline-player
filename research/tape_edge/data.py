"""Datenbeschaffung: Databento-Download mit Kostencheck, Parquet-Cache, synthetische Ticks.

Benoetigt fuer echte Daten die Umgebungsvariable DATABENTO_API_KEY.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATASET = "GLBX.MDP3"
COLUMNS = ["ts_ms", "price", "size", "side"]


def fetch(symbol: str, start: str, end: str, schema: str, out: Path, confirm: bool,
          max_cost_usd: float) -> Path | None:
    """Fragt erst die Kosten ab, laedt nur mit confirm=True und unter max_cost_usd."""
    import databento as db  # erst hier, damit der Rest ohne das Paket laeuft

    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        sys.exit("DATABENTO_API_KEY fehlt (Umgebungsvariable).")
    client = db.Historical(key)
    kwargs = dict(dataset=DATASET, symbols=[symbol], schema=schema,
                  start=start, end=end, stype_in="continuous")
    cost = client.metadata.get_cost(**kwargs)
    size_gb = client.metadata.get_billable_size(**kwargs) / 1e9
    print(f"{symbol} {schema} {start}..{end}: {size_gb:.2f} GB, Kosten {cost:.2f} USD")
    if not confirm:
        print("Kein Download (--confirm fehlt).")
        return None
    if cost > max_cost_usd:
        sys.exit(f"Kosten {cost:.2f} USD ueber Limit {max_cost_usd:.2f} USD. Abbruch.")
    store = client.timeseries.get_range(**kwargs)
    df = store.to_df()
    df = df.reset_index()
    ts_col = "ts_recv" if "ts_recv" in df.columns else "ts_event"
    out_df = pd.DataFrame({
        "ts_ms": (df[ts_col].astype("int64") // 1_000_000).astype("int64"),
        "price": df["price"].astype("float64"),
        "size": df["size"].astype("float64"),
        "side": df["side"].map({"B": 1, "A": -1}).fillna(0).astype("int8"),
    })
    if schema == "tbbo" and "bid_px_00" in df.columns:
        out_df["bid"] = df["bid_px_00"].astype("float64")
        out_df["ask"] = df["ask_px_00"].astype("float64")
    out.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_parquet(out, index=False)
    print(f"gespeichert: {out} ({len(out_df):,} Trades)")
    return out


def synthetic(days: int = 3, trades_per_day: int = 150_000, seed: int = 7,
              mintick: float = 0.25, start_price: float = 5000.0) -> pd.DataFrame:
    """Synthetische Ticks ohne Edge: Random Walk mit Bid/Ask-Bounce und Aggressor-Flag.

    Dient nur dem Funktionstest. Ein korrekter Test darf hier keinen Edge finden.
    """
    rng = np.random.default_rng(seed)
    frames = []
    base_day = pd.Timestamp("2026-09-01", tz="UTC")
    mid = start_price
    for d in range(days):
        day = base_day + pd.Timedelta(days=d)
        while day.weekday() >= 5:
            day += pd.Timedelta(days=1)
            base_day += pd.Timedelta(days=1)
        # RTH 13:30-20:00 UTC, Ankunftszeiten als Poisson-Prozess mit Clustern
        t0 = int((day + pd.Timedelta(hours=13, minutes=30)).value // 1_000_000)
        span = 6.5 * 3600 * 1000
        gaps = rng.exponential(span / trades_per_day / 0.7, trades_per_day)
        gaps[rng.random(trades_per_day) < 0.3] = 0  # Sweeps: mehrere Fills in derselben ms
        ts = t0 + np.cumsum(gaps).astype(np.int64)
        ts = ts[ts < t0 + span]
        n = len(ts)
        side = np.where(rng.random(n) < 0.5, 1, -1).astype(np.int8)
        # Bid = base, Ask = base + 1 Tick. Kaeufer handeln am Ask, Verkaeufer am Bid.
        steps = rng.choice([-1, 0, 1], size=n, p=[0.02, 0.96, 0.02])
        base = mid + mintick * np.cumsum(steps)
        price = np.where(side > 0, base + mintick, base)
        mid_path = base
        size = rng.geometric(0.3, n).astype(np.float64)
        frames.append(pd.DataFrame({"ts_ms": ts, "price": price, "size": size, "side": side}))
        mid = float(mid_path[-1])
    return pd.concat(frames, ignore_index=True)


def load(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df = df[[c for c in COLUMNS + ["bid", "ask"] if c in df.columns]]
    return df.sort_values("ts_ms", kind="stable").reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser(description="Databento-Download mit Kostencheck")
    ap.add_argument("--symbol", default="ES.v.0", help="fortlaufender Kontrakt, Volumen-Roll")
    ap.add_argument("--start", required=True, help="z. B. 2026-08-01")
    ap.add_argument("--end", required=True, help="z. B. 2026-11-01")
    ap.add_argument("--schema", default="trades", choices=["trades", "tbbo"])
    ap.add_argument("--out", default=None, help="Parquet-Ziel")
    ap.add_argument("--confirm", action="store_true", help="wirklich laden")
    ap.add_argument("--max-cost", type=float, default=20.0, help="Kostenlimit in USD")
    a = ap.parse_args()
    out = Path(a.out) if a.out else Path("data") / f"{a.symbol}_{a.schema}_{a.start}_{a.end}.parquet"
    fetch(a.symbol, a.start, a.end, a.schema, out, a.confirm, a.max_cost)


if __name__ == "__main__":
    main()
