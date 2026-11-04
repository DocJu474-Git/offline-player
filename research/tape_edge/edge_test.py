"""Edge-Test der Tape-Volume-Algorithmen auf Tick-Daten.

Ablauf:
  1. Ticks laden (Parquet aus data.py) oder synthetisch erzeugen (--synthetic).
  2. Algorithmen berechnen, einmal mit Tick-Regel, einmal mit Boersen-Flag.
  3. Signale: Nullkreuzung (trendbereinigt) und Divergenz zum Preis.
  4. Vorwaertsrenditen nach Sekunden und Ticks, Vergleich gegen Zufallseinstiege,
     Kosten (Spread + Kommission), Stabilitaet je Monat, Information Coefficient.
  5. PDF-Report und CSV.

Beispiel:
  python edge_test.py --synthetic --out report
  python edge_test.py --data data/ES.v.0_trades_2026-08-01_2026-11-01.parquet --out report
"""
from __future__ import annotations

import argparse
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from algos import ALGO_NAMES, compute
import data as data_mod

SEC_HORIZONS = [5, 20, 60]
TICK_HORIZONS = [20, 100]
SIGNALS = ["Nullkreuzung", "Divergenz"]


# ----------------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------------
def rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    cs = np.cumsum(np.concatenate(([0.0], x.astype(np.float64))))
    out = np.full(x.shape[0], np.nan)
    out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


def rolling_max(x: np.ndarray, w: int) -> np.ndarray:
    return pd.Series(x).rolling(w, min_periods=w).max().to_numpy()


def rolling_min(x: np.ndarray, w: int) -> np.ndarray:
    return pd.Series(x).rolling(w, min_periods=w).min().to_numpy()


def debounce(idx: np.ndarray, min_gap: int) -> np.ndarray:
    """Behaelt von dicht aufeinander folgenden Ereignissen nur das erste."""
    keep = []
    last = -min_gap - 1
    for i in idx:
        if i - last >= min_gap:
            keep.append(i)
            last = i
    return np.asarray(keep, dtype=np.int64)


def forward_returns(price: np.ndarray, ts_ms: np.ndarray, day: np.ndarray, mintick: float):
    """Vorwaertsrendite in Ticks je Horizont, Mid-zu-Mid, NaN wenn der Horizont die Session verlaesst.

    ``price`` ist hier der Mid-Preis. Last-zu-Last wuerde den Bid/Ask-Bounce als
    Scheinertrag messen: ein Trade am Hoch liegt am Ask und faellt danach im Mittel
    um einen halben Spread zurueck.
    """
    n = len(price)
    res = {}
    for h in SEC_HORIZONS:
        j = np.searchsorted(ts_ms, ts_ms + h * 1000, side="left")
        j = np.minimum(j, n - 1)
        r = (price[j] - price) / mintick
        r[(day[j] != day) | (j <= np.arange(n))] = np.nan
        res[f"{h}s"] = r
    for k in TICK_HORIZONS:
        r = np.full(n, np.nan)
        r[:-k] = (price[k:] - price[:-k]) / mintick
        same = np.zeros(n, dtype=bool)
        same[:-k] = day[k:] == day[:-k]
        r[~same] = np.nan
        res[f"{k}t"] = r
    return res


def event_stats(dirs: np.ndarray, idx: np.ndarray, fwd: dict, cost_ticks: float):
    rows = {}
    for h, r in fwd.items():
        x = dirs * r[idx]
        x = x[~np.isnan(x)]
        n = len(x)
        if n < 5:
            rows[h] = dict(n=n, mean=np.nan, hit=np.nan, t=np.nan, net=np.nan)
            continue
        m = x.mean()
        s = x.std(ddof=1)
        t = m / (s / math.sqrt(n)) if s > 0 else np.nan
        rows[h] = dict(n=n, mean=m, hit=(x > 0).mean(), t=t, net=m - cost_ticks)
    return rows


def random_baseline(rng, pool: np.ndarray, n: int, fwd: dict, draws: int):
    """Verteilung des mittleren Ertrags zufaelliger Einstiege gleicher Anzahl."""
    out = {h: np.empty(draws) for h in fwd}
    for d in range(draws):
        idx = rng.choice(pool, size=n, replace=True)
        dirs = rng.choice(np.array([-1.0, 1.0]), size=n)
        for h, r in fwd.items():
            x = dirs * r[idx]
            out[h][d] = np.nanmean(x)
    return out


def spearman_ic(x: np.ndarray, r: np.ndarray, day: np.ndarray, lag: int, step: int):
    """Information Coefficient je Tag: Rangkorrelation Algo-Aenderung vs. Vorwaertsrendite."""
    ics = []
    n = len(x)
    sample = np.arange(lag, n, step)
    dx = x[sample] - x[sample - lag]
    rr = r[sample]
    dd = day[sample]
    for d in np.unique(dd):
        m = (dd == d) & ~np.isnan(rr)
        if m.sum() < 30:
            continue
        a = pd.Series(dx[m]).rank().to_numpy()
        b = pd.Series(rr[m]).rank().to_numpy()
        if a.std() == 0 or b.std() == 0:
            continue
        ics.append(np.corrcoef(a, b)[0, 1])
    return np.asarray(ics)


# ----------------------------------------------------------------------------
# Studie
# ----------------------------------------------------------------------------
def run_study(df: pd.DataFrame, args):
    rng = np.random.default_rng(args.seed)
    price = df["price"].to_numpy(np.float64)
    size = df["size"].to_numpy(np.float64)
    ts_ms = df["ts_ms"].to_numpy(np.int64)
    side_flag = df["side"].to_numpy(np.int8)
    n = len(price)

    local = pd.to_datetime(ts_ms, unit="ms", utc=True).tz_convert("America/Chicago")
    day = (np.asarray(local.year) * 400 + np.asarray(local.dayofyear)).astype(np.int64)
    month = local.strftime("%Y-%m").to_numpy()
    minutes = local.hour * 60 + local.minute
    rth = (minutes >= 8 * 60 + 30) & (minutes < 15 * 60)

    if "bid" in df.columns and "ask" in df.columns:
        mid = (df["bid"].to_numpy(np.float64) + df["ask"].to_numpy(np.float64)) / 2
        mid_source = "Bid/Ask aus TBBO"
    else:
        from algos import tick_rule_side
        s_mid = side_flag if (side_flag != 0).mean() > 0.9 else tick_rule_side(price)
        mid = price - s_mid * args.mintick / 2
        mid_source = "Naeherung: Last minus halber Tick in Aggressor-Richtung"
    fwd = forward_returns(mid, ts_ms, day, args.mintick)
    pool = np.flatnonzero(rth & ~np.isnan(fwd["60s"]))
    cost_ticks = 1.0 + 2 * args.commission / args.tick_value  # 1 Tick Spread + 2 Seiten Kommission
    flag_available = (side_flag != 0).mean() > 0.9

    modes = ["tick"] + (["flag"] if flag_available else [])
    results = []
    ic_rows = []
    monthly = []
    series_for_plot = {}
    W = args.window
    for mode in modes:
        mat, _ = compute(price, size, ts_ms, side_flag=side_flag, side_mode=mode,
                         mintick=args.mintick)
        for a, name in enumerate(ALGO_NAMES):
            x = mat[:, a]
            if mode == "tick" and a in (0, 3, 8):
                series_for_plot[name] = x.astype(np.float32)
            if mode == "flag" and a not in (3, 8):
                continue  # nur dort, wo die Seite eingeht
            # Signal 1: Nullkreuzung des trendbereinigten Algos
            z = x - rolling_mean(x, W)
            sgn = np.sign(z)
            cross = np.flatnonzero((sgn[1:] != sgn[:-1]) & (sgn[1:] != 0) & ~np.isnan(z[1:])) + 1
            cross = cross[rth[cross]]
            cross = debounce(cross, args.min_gap)
            d_cross = sgn[cross]
            # Signal 2: Divergenz Preis vs. Algo ueber Lookback L
            L = args.lookback
            # Preis setzt ein Lookback-Hoch, der Algo liegt mindestens ein Viertel
            # seiner Lookback-Spanne unter seinem Hoch (bzw. ueber seinem Tief).
            p_hi = price >= rolling_max(price, L)
            p_lo = price <= rolling_min(price, L)
            x_max = rolling_max(x, L)
            x_min = rolling_min(x, L)
            x_rng = x_max - x_min
            x_weak = x <= x_max - args.div_frac * x_rng
            x_strong = x >= x_min + args.div_frac * x_rng
            bear = np.flatnonzero(p_hi & x_weak & rth)
            bull = np.flatnonzero(p_lo & x_strong & rth)
            bear = debounce(bear, args.min_gap)
            bull = debounce(bull, args.min_gap)
            div_idx = np.concatenate([bear, bull])
            d_div = np.concatenate([-np.ones(len(bear)), np.ones(len(bull))])
            order = np.argsort(div_idx)
            div_idx, d_div = div_idx[order], d_div[order]

            for sig, idx, dirs in ((SIGNALS[0], cross, d_cross), (SIGNALS[1], div_idx, d_div)):
                st = event_stats(dirs, idx, fwd, cost_ticks)
                base = random_baseline(rng, pool, max(len(idx), 5), fwd, args.draws)
                for h, s in st.items():
                    p = np.nan
                    if not np.isnan(s["mean"]):
                        p = float((np.abs(base[h]) >= abs(s["mean"])).mean())
                    results.append(dict(mode=mode, algo=name, signal=sig, horizon=h, n=s["n"],
                                        mean_ticks=s["mean"], hit=s["hit"], t=s["t"],
                                        net_ticks=s["net"], p_random=p,
                                        rand_mean=float(np.nanmean(base[h]))))
                # Stabilitaet je Monat (Horizont 60s)
                for mo in np.unique(month[idx]) if len(idx) else []:
                    m = month[idx] == mo
                    xx = dirs[m] * fwd["60s"][idx[m]]
                    xx = xx[~np.isnan(xx)]
                    if len(xx) >= 5:
                        monthly.append(dict(mode=mode, algo=name, signal=sig, month=mo,
                                            n=len(xx), mean_ticks=xx.mean()))
            ic = spearman_ic(x, fwd["60s"], day, lag=args.ic_lag, step=args.ic_step)
            ic_rows.append(dict(mode=mode, algo=name, days=len(ic),
                                ic_mean=float(ic.mean()) if len(ic) else np.nan,
                                ic_t=float(ic.mean() / ic.std(ddof=1) * math.sqrt(len(ic)))
                                if len(ic) > 2 and ic.std(ddof=1) > 0 else np.nan))
        del mat

    meta = dict(n_trades=n, days=int(len(np.unique(day))), start=str(local[0])[:16],
                end=str(local[-1])[:16], cost_ticks=cost_ticks, flag=flag_available,
                rth_share=float(rth.mean()), mid_source=mid_source)
    return (pd.DataFrame(results), pd.DataFrame(ic_rows), pd.DataFrame(monthly),
            meta, series_for_plot, price, local, day)


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------
def make_report(res, ics, monthly, meta, series, price, local, day, args, out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                         "axes.spines.top": False, "axes.spines.right": False})
    ink, muted, accent = "#1f2933", "#6b7280", "#2563eb"

    def page(title, subtitle=None):
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.text(0.07, 0.955, title, fontsize=16, weight="bold", color=ink)
        if subtitle:
            fig.text(0.07, 0.935, subtitle, fontsize=9, color=muted)
        fig.text(0.07, 0.03, f"Tape Volume Edge-Test · {datetime.now():%d.%m.%Y %H:%M} · "
                 f"{'synthetische Daten' if args.synthetic else Path(args.data).name}",
                 fontsize=7, color=muted)
        return fig

    def table(fig, df, y_top, col_labels, widths, height=0.02, fmt=None):
        ax = fig.add_axes([0.07, y_top - height * (len(df) + 1), 0.86, height * (len(df) + 1)])
        ax.axis("off")
        cells = []
        for _, r in df.iterrows():
            cells.append([fmt[c](r[c]) if fmt and c in fmt else str(r[c]) for c in df.columns])
        tb = ax.table(cellText=cells, colLabels=col_labels, colWidths=widths, loc="upper left",
                      cellLoc="right")
        tb.auto_set_font_size(False)
        tb.set_fontsize(7.5)
        tb.scale(1, 1.25)
        for (r, c), cell in tb.get_celld().items():
            cell.set_edgecolor("#e5e7eb")
            if r == 0:
                cell.set_text_props(weight="bold", color=ink)
                cell.set_facecolor("#f3f4f6")
            elif c == 0 or isinstance(cells[r - 1][c], str) and not cells[r - 1][c].replace(".", "").replace("-", "").isdigit():
                cell.set_text_props(ha="left")
        return y_top - height * (len(df) + 1)

    f2 = lambda v: "" if pd.isna(v) else f"{v:.2f}"
    f3 = lambda v: "" if pd.isna(v) else f"{v:.3f}"
    fi = lambda v: "" if pd.isna(v) else f"{int(v):,}".replace(",", ".")
    fp = lambda v: "" if pd.isna(v) else f"{100 * v:.0f} %"

    with PdfPages(out) as pdf:
        # ---- Seite 1: Zusammenfassung ----
        fig = page("Tape Volume: Edge-Test", "Ereignisstudie der acht Algorithmen auf Tick-Daten")
        facts = [
            ("Daten", "synthetischer Random Walk (kein Edge erwartet)" if args.synthetic else Path(args.data).name),
            ("Zeitraum", f"{meta['start']} bis {meta['end']} (Chicago), {meta['days']} Handelstage"),
            ("Trades", fi(meta["n_trades"]) + f", davon RTH {fp(meta['rth_share'])}"),
            ("Aggressor-Flag", "vorhanden, Vergleich Tick-Regel vs. Flag" if meta["flag"] else "nicht vorhanden, nur Tick-Regel"),
            ("Kosten je Trade", f"{meta['cost_ticks']:.2f} Ticks (1 Tick Spread + 2 x {args.commission:.2f} USD Kommission bei {args.tick_value:.2f} USD/Tick)"),
            ("Ertragsmessung", f"Mid-zu-Mid, {meta['mid_source']}"),
            ("Signale", f"Nullkreuzung (Fenster {args.window} Ticks), Divergenz (Lookback {args.lookback} Ticks, Algo-Abstand {100*args.div_frac:.0f} % der Spanne), Abstand min. {args.min_gap} Ticks"),
            ("Zufallsbasis", f"{args.draws} Ziehungen gleicher Anzahl mit zufaelliger Richtung"),
        ]
        y = 0.90
        import textwrap
        for k, v in facts:
            fig.text(0.07, y, k, fontsize=9, weight="bold", color=ink)
            lines = textwrap.wrap(v, 95)
            for ln in lines:
                fig.text(0.27, y, ln, fontsize=9, color=ink)
                y -= 0.019
            y -= 0.006
        fig.text(0.07, y - 0.01, "Beste Signale nach Netto-Ertrag (60 s), sortiert", fontsize=11, weight="bold", color=ink)
        best = res[res.horizon == "60s"].sort_values("net_ticks", ascending=False).head(12)
        best = best[["mode", "algo", "signal", "n", "mean_ticks", "hit", "t", "p_random", "net_ticks"]]
        y = table(fig, best, y - 0.025, ["Seite", "Algorithmus", "Signal", "n", "Ø Ticks", "Treffer", "t", "p Zufall", "Netto"],
                  [0.08, 0.22, 0.14, 0.09, 0.1, 0.1, 0.08, 0.1, 0.09],
                  fmt=dict(n=fi, mean_ticks=f2, hit=fp, t=f2, p_random=f3, net_ticks=f2))
        fig.text(0.07, y - 0.03, "Lesehilfe", fontsize=11, weight="bold", color=ink)
        notes = [
            "Ø Ticks: mittlere Bewegung in Signalrichtung nach dem Horizont, vor Kosten.",
            "t: t-Statistik des Mittelwerts. Werte ueber 3 bei n ueber 1000 sind ernst zu nehmen.",
            "p Zufall: Anteil zufaelliger Einstiege mit mindestens gleich grossem |Ø|. Unter 0,01 heisst: selten durch Zufall.",
            "Netto: Ø Ticks minus Kosten. Nur positive Netto-Werte mit kleinem p und stabilen Monaten sind ein Edge-Kandidat.",
            "FAKT sind nur die Zahlen. Die Einordnung als Edge bleibt eine ANNAHME, bis sie out-of-sample bestaetigt ist.",
        ]
        y -= 0.05
        for t in notes:
            fig.text(0.07, y, "· " + t, fontsize=8.5, color=ink)
            y -= 0.02
        pdf.savefig(fig)
        plt.close(fig)

        # ---- Seite 2: Beispieltag ----
        fig = page("Beispieltag", "Preis und drei Algorithmen (Tick-Regel), auf -1..+1 normiert")
        d0 = np.unique(day)[min(1, len(np.unique(day)) - 1)]
        m = day == d0
        idx = np.flatnonzero(m)
        t = local[idx]
        ax = fig.add_axes([0.1, 0.62, 0.84, 0.28])
        ax.plot(t, price[idx], color=ink, lw=0.6)
        ax.set_ylabel("Preis")
        ax.set_title(str(t[0])[:10], fontsize=9, color=muted, loc="left")
        ax2 = fig.add_axes([0.1, 0.3, 0.84, 0.28], sharex=ax)
        for name, col in zip(series, [accent, "#f59e0b", "#10b981"]):
            s = series[name][idx].astype(np.float64)
            s = (2 * s - s.max() - s.min()) / (s.max() - s.min()) if s.max() > s.min() else s * 0
            ax2.plot(t, s, lw=0.7, color=col, label=name)
        ax2.axhline(0, color=muted, lw=0.5)
        ax2.legend(loc="upper left", frameon=False, fontsize=8)
        ax2.set_ylabel("Algo (normiert)")
        fig.text(0.1, 0.22, "Kumulative Algorithmen laufen ueber den Tag. Sichtbar wird nur Richtung und Divergenz, keine absolute Schwelle.",
                 fontsize=8.5, color=ink)
        pdf.savefig(fig)
        plt.close(fig)

        # ---- Seiten je Algorithmus ----
        for name in ALGO_NAMES:
            sub = res[res.algo == name]
            if sub.empty:
                continue
            fig = page(name, "Ereignisstudie je Signal und Horizont")
            y = 0.90
            for mode in sub["mode"].unique():
                ss = sub[sub["mode"] == mode].copy()
                fig.text(0.07, y, "Seite: " + ("Tick-Regel" if mode == "tick" else "Boersen-Flag"), fontsize=10, weight="bold", color=ink)
                ss = ss[["signal", "horizon", "n", "mean_ticks", "hit", "t", "p_random", "rand_mean", "net_ticks"]]
                y = table(fig, ss, y - 0.015, ["Signal", "Horizont", "n", "Ø Ticks", "Treffer", "t", "p Zufall", "Ø Zufall", "Netto"],
                          [0.16, 0.1, 0.1, 0.1, 0.1, 0.08, 0.1, 0.1, 0.09],
                          fmt=dict(n=fi, mean_ticks=f2, hit=fp, t=f2, p_random=f3, rand_mean=f2, net_ticks=f2))
                y -= 0.035
            icr = ics[ics.algo == name]
            if not icr.empty:
                fig.text(0.07, y, "Information Coefficient (Rangkorrelation Algo-Aenderung vs. 60-s-Rendite, je Tag)", fontsize=10, weight="bold", color=ink)
                y = table(fig, icr[["mode", "days", "ic_mean", "ic_t"]], y - 0.015, ["Seite", "Tage", "Ø IC", "t"],
                          [0.2, 0.2, 0.2, 0.2], fmt=dict(days=fi, ic_mean=f3, ic_t=f2))
                y -= 0.035
            mo = monthly[monthly.algo == name]
            if not mo.empty:
                fig.text(0.07, y, "Stabilitaet je Monat (60 s, Ø Ticks vor Kosten)", fontsize=10, weight="bold", color=ink)
                piv = mo.pivot_table(index=["mode", "signal"], columns="month", values="mean_ticks").reset_index()
                cols = [c for c in piv.columns if c not in ("mode", "signal")]
                y = table(fig, piv, y - 0.015, ["Seite", "Signal"] + cols,
                          [0.12, 0.18] + [0.7 / max(len(cols), 1)] * len(cols),
                          fmt={c: f2 for c in cols})
            pdf.savefig(fig)
            plt.close(fig)

        # ---- Letzte Seite: Methodik ----
        fig = page("Methodik und Grenzen")
        text = [
            "Algorithmen: Port des thinkScript-Studies Tape Volume v1.1 auf einzelne Trades. Zukunftsbezuege des Originals",
            "(Rueckwaerts-Indexer, HighestAll) sind kausal ersetzt. Volume Momentum und Transaction Sentiment sind Naeherungen.",
            "",
            "Seite: Tick-Regel schaetzt Kaeufer/Verkaeufer aus der Preisrichtung. Boersen-Flag ist das Aggressor-Feld der CME.",
            "Die Seite geht in Supply & Demand und die Delta-Referenz ein, die uebrigen Algorithmen sind preisbasiert.",
            "",
            "Signale: Nullkreuzung des Algos minus gleitendem Mittel; Divergenz, wenn der Preis ein Lookback-Hoch/-Tief setzt,",
            "der Algo aber nicht. Ereignisse nur in der RTH (08:30-15:00 Chicago), Mindestabstand zwischen Ereignissen.",
            "",
            "Ertrag: Mid-Preis-Aenderung in Signalrichtung nach Horizont, in Ticks, innerhalb derselben Session. Last-zu-Last",
            "wuerde den Bid/Ask-Bounce als Scheinertrag messen. Kosten: ein Tick Spread fuer den Round-Trip plus Kommission",
            "beider Seiten. Slippage ueber den Spread hinaus ist nicht enthalten.",
            "",
            "Zufallsbasis: gleiche Anzahl zufaelliger RTH-Einstiege mit zufaelliger Richtung, wiederholt gezogen. p Zufall ist der",
            "Anteil der Ziehungen mit mindestens gleich grossem Betrag des Mittelwerts.",
            "",
            "Grenzen: Parameter (Fenster, Lookback, Abstand) sind nicht optimiert, daher auch nicht overfittet. Mehrere Signale",
            "und Horizonte werden gleichzeitig getestet, einzelne kleine p-Werte sind dadurch zu erwarten (Multiple Testing).",
            "Ein Edge-Kandidat braucht: positives Netto, p unter 0,01, gleiches Vorzeichen in allen Monaten, n ueber 1000.",
        ]
        y = 0.90
        for line in text:
            fig.text(0.07, y, line, fontsize=8.5, color=ink)
            y -= 0.02
        pdf.savefig(fig)
        plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description="Tape Volume Edge-Test")
    ap.add_argument("--data", help="Parquet mit ts_ms, price, size, side")
    ap.add_argument("--synthetic", action="store_true", help="synthetische Ticks statt Daten")
    ap.add_argument("--synthetic-days", type=int, default=3)
    ap.add_argument("--out", default="report", help="Ausgabeordner")
    ap.add_argument("--mintick", type=float, default=0.25)
    ap.add_argument("--tick-value", type=float, default=12.5, help="USD je Tick (ES 12,5, MES 1,25)")
    ap.add_argument("--commission", type=float, default=2.0, help="USD je Seite")
    ap.add_argument("--window", type=int, default=2000, help="Ticks fuer Trendbereinigung")
    ap.add_argument("--lookback", type=int, default=2000, help="Ticks fuer Divergenz")
    ap.add_argument("--min-gap", type=int, default=500, help="Mindestabstand Ereignisse in Ticks")
    ap.add_argument("--div-frac", type=float, default=0.25, help="Divergenz: Algo-Abstand vom Extrem als Anteil der Spanne")
    ap.add_argument("--draws", type=int, default=300, help="Zufallsziehungen")
    ap.add_argument("--ic-lag", type=int, default=500)
    ap.add_argument("--ic-step", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-days", type=int, default=0, help="nur die letzten N Handelstage (0 = alle)")
    args = ap.parse_args()

    if args.synthetic:
        df = data_mod.synthetic(days=args.synthetic_days)
    elif args.data:
        df = data_mod.load(Path(args.data))
    else:
        ap.error("--data oder --synthetic angeben")

    if args.max_days > 0:
        d = pd.to_datetime(df["ts_ms"], unit="ms", utc=True).dt.tz_convert("America/Chicago").dt.date
        keep = sorted(d.unique())[-args.max_days:]
        df = df[d.isin(keep)].reset_index(drop=True)
    print(f"{len(df):,} Trades geladen")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    res, ics, monthly, meta, series, price, local, day = run_study(df, args)
    res.to_csv(out / "ergebnisse.csv", index=False)
    ics.to_csv(out / "ic.csv", index=False)
    monthly.to_csv(out / "monate.csv", index=False)
    make_report(res, ics, monthly, meta, series, price, local, day, args, out / "tape_volume_edge_report.pdf")
    print(res[res.horizon == "60s"].sort_values("net_ticks", ascending=False)
          [["mode", "algo", "signal", "n", "mean_ticks", "hit", "t", "p_random", "net_ticks"]].to_string(index=False))
    print(f"\nReport: {out / 'tape_volume_edge_report.pdf'}")


if __name__ == "__main__":
    main()
