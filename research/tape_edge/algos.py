"""Tape-Volume-Algorithmen auf einem Tick-Strom.

Port der acht Algorithmen aus dem thinkScript-Study "Tape Volume" v1.1
(Logan Crist / BaconTrading) als sequentielle Zustandsmaschine. Eingabe sind
einzelne Trades: Preis, Groesse, Zeitstempel (ms) und Aggressor-Seite.

side: +1 Buy-Aggressor, -1 Sell-Aggressor, 0 unbekannt.
Mit ``side_mode="tick"`` wird die Seite aus der Tick-Richtung geschaetzt
(Tick-Regel), mit ``side_mode="flag"`` wird das Boersen-Flag verwendet.
Die Seite beeinflusst Supply & Demand und die beiden Delta-Referenzen,
alle anderen Algorithmen arbeiten wie im Original rein preisbasiert.
"""
from __future__ import annotations

import numpy as np

try:
    from numba import njit
except ImportError:  # pragma: no cover - Fallback ohne numba (sehr langsam)
    def njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda f: f

ALGO_NAMES = [
    "Cash Flow",
    "Volume Momentum",
    "Order Accumulation",
    "Supply & Demand",
    "Transaction Sentiment",
    "Market Orders",
    "Order Speed",
    "Price Inertia",
    "Delta (Referenz)",
]
N_ALGOS = len(ALGO_NAMES)


@njit(cache=True)
def tick_rule_side(price):
    """Tick-Regel: Uptick = +1, Downtick = -1, Zero-Tick = letzte Richtung."""
    n = price.shape[0]
    side = np.zeros(n, dtype=np.int8)
    last = 0
    for i in range(1, n):
        if price[i] > price[i - 1]:
            last = 1
        elif price[i] < price[i - 1]:
            last = -1
        side[i] = last
    return side


@njit(cache=True)
def run_algos(price, size, side, ts_ms, mintick, rsi_len, sd_cycle, sd_sum_len, pi_reverse):
    """Berechnet alle Algorithmen. Rueckgabe: Matrix (n, N_ALGOS)."""
    n = price.shape[0]
    out = np.zeros((n, 9))

    # 7A Cash Flow
    mean_rec = 0.0
    range_hi = 0.0
    range_lo = 0.0
    range_mean = 0.0
    range_value_sum = 0.0
    range_vol_sum = 0.0
    range_vwap = 0.0
    vwap_diff = 0.0
    break_effect = 0.0
    # 7B Volume Momentum
    last_d = 0.0
    prev_d = np.nan
    box_mean = 0.0
    vwap_vol_sum = 0.0
    vwap_price_sum = 0.0
    vwap1 = 0.0
    vwap_bar_sum = 0.0
    # 7C Order Accumulation
    a_f = 2.0 / (rsi_len + 1)
    a_s = 2.0 / (rsi_len * 4 + 1)
    net_f = 0.0
    tot_f = 0.0
    net_s = 0.0
    tot_s = 0.0
    rsi_f = 0.0
    rsi_s = 0.0
    oa_sum = 0.0
    # 7D Supply & Demand
    chart_vol = 0.0
    last_tick = 0
    buf = np.zeros(sd_sum_len)
    buf_pos = 0
    buf_fill = 0
    demand_sum = 0.0
    cyc_a = 0.0
    cyc_b = 0.0
    demand_a = 0.0
    demand_b = 0.0
    sd_sum = 0.0
    # 7E/7F
    market_orders = 0.0
    vdir_sum = 0.0
    vd1 = np.nan
    vd2 = np.nan
    data_up = 0.0
    data_dn = 0.0
    price_state = 1
    p_up = 0.0
    p_dn = 0.0
    diff_sum = 0.0
    # 7G
    up_time = 0.0
    dn_time = 0.0
    # 7H
    dn_mem = 0.0
    up_mem = 0.0
    down_sum = 0.0
    up_sum = 0.0
    order_diff = 0.0
    # Referenz
    delta = 0.0

    pc = 0.0
    pt = 0
    pday = -1

    for i in range(n):
        c = price[i]
        v = size[i]
        t = ts_ms[i]
        first = i == 0
        if first:
            pc = c
            pt = t
        dt = 0 if first else t - pt
        day = t // 86400000
        new_day = first or day != pday
        sd = side[i]

        # ---- 7A Cash Flow ----
        mean_prev = mean_rec
        if first:
            mean_rec = c
        elif c != pc:
            mean_rec = (c + pc) / 2.0
        hi = range_hi
        lo = range_lo
        gap = 0
        if first:
            hi = c
            lo = c
        elif abs(c - pc) > mintick:
            if c > pc:
                gap = 1
                hi = c
                lo = c - mintick
            else:
                gap = -1
                hi = c + mintick
                lo = c
        elif mean_rec != mean_prev:
            hi = mean_rec + mintick / 2.0
            lo = mean_rec - mintick / 2.0
        rm = (hi + lo) / 2.0
        changed = first or rm != range_mean
        if changed:
            rvs = v * rm
            rvol = v
        else:
            rvs = v * c + range_value_sum
            rvol = v + range_vol_sum
        rvwap = rvs / rvol if rvol != 0 else rm
        if changed:
            vd = v * (rvwap - rm)
        else:
            vd = vwap_diff + v * (rvwap - rm)
        be = break_effect
        if (not first) and changed:
            pm = range_mean
            pv = range_vwap
            pd = vwap_diff
            if rm > pm:
                if pv > pm:
                    be = be + pd + v * mintick / 2.0
                elif pv < pm:
                    be = be - pd - v * mintick / 2.0
            else:
                if pv > pm:
                    be = be - pd + v * mintick / 2.0
                elif pv < pm:
                    be = be + pd - v * mintick / 2.0

        # ---- 7B Volume Momentum ----
        pivot = False
        if first:
            last_d = c
        elif c != last_d:
            if (not np.isnan(prev_d)) and (c - last_d) * (last_d - prev_d) < 0:
                pivot = True
            prev_d = last_d
            last_d = c
        if first:
            bm = c
        elif pivot:
            bm = (c + prev_d) / 2.0
        else:
            bm = box_mean
        box_changed = first or bm != box_mean
        if box_changed:
            vvs = v
            vps = v * bm
        else:
            vvs = vwap_vol_sum + v
            vps = vwap_price_sum + v * c
        vw1 = vps / vvs if vvs != 0 else bm
        if first:
            vbs = v * (vw1 - bm)
        else:
            vbs = vwap_bar_sum + v * (vw1 - bm)

        # ---- 7C Order Accumulation ----
        chg = 0.0 if first else c - pc
        if first:
            net_f = chg
            tot_f = abs(chg)
            net_s = chg
            tot_s = abs(chg)
        else:
            net_f += a_f * (chg - net_f)
            tot_f += a_f * (abs(chg) - tot_f)
            net_s += a_s * (chg - net_s)
            tot_s += a_s * (abs(chg) - tot_s)
        rf = 50.0 * net_f / tot_f if tot_f != 0 else 0.0
        rs = 50.0 * net_s / tot_s if tot_s != 0 else 0.0
        if rs > 0 and rf < 0:
            score = v
        elif rs < 0 and rf > 0:
            score = -v
        elif rs <= rsi_s and rf > rsi_f:
            score = v
        elif rs >= rsi_s and rf < rsi_f:
            score = -v
        elif rs > rsi_s and rf > rsi_f:
            score = v
        elif rs < rsi_s and rf < rsi_f:
            score = -v
        else:
            score = 0.0
        oa_sum += score
        rsi_f = rf
        rsi_s = rs

        # ---- 7D Supply & Demand ----
        chart_vol = 1.0 if new_day else chart_vol + 1.0
        if sd != 0:
            last_tick = sd
        time_w = 0.0 if (new_day or dt == 0) else v * dt
        term = 0.0
        if last_tick == -1:
            term += v - 2.0 * time_w
        if last_tick == 1:
            term -= v - 2.0 * time_w
        if buf_fill < sd_sum_len:
            buf[buf_pos] = term
            demand_sum += term
            buf_fill += 1
        else:
            demand_sum += term - buf[buf_pos]
            buf[buf_pos] = term
        buf_pos = (buf_pos + 1) % sd_sum_len
        cyc = float(sd_cycle)
        half = cyc / 2.0
        ca = chart_vol - cyc * np.floor(chart_vol / cyc)
        cb = (chart_vol - half) - cyc * np.floor((chart_vol - half) / cyc)
        if (not first) and ca < cyc_a:
            demand_a = demand_sum
        else:
            demand_a += demand_sum
        if (not first) and cb < cyc_b:
            demand_b = demand_sum
        else:
            demand_b += demand_sum
        frac = (chart_vol - half * np.floor(chart_vol / half)) / half
        if ca >= cb:
            phased = demand_b * frac + demand_a * (1.0 - frac)
        else:
            phased = demand_a * frac + demand_b * (1.0 - frac)
        sd_sum = phased if first else sd_sum + phased
        cyc_a = ca
        cyc_b = cb

        # ---- 7F Market Orders / 7E Transaction Sentiment ----
        vdir = v if vw1 > bm else (-v if vw1 < bm else 0.0)
        long_m = v if ((not first) and (rm > range_mean or gap == 1)) else 0.0
        short_m = v if ((not first) and (rm < range_mean or gap == -1)) else 0.0
        market_orders += long_m - short_m
        if not (dt != 0 or c == pc):
            vdir_sum += vdir
        vd_sum = market_orders + vdir_sum
        pivot_prev = (not np.isnan(vd2)) and (vd1 - vd_sum) * (vd1 - vd2) > 0
        if first or pivot_prev:
            data_up = 0.0
            data_dn = 0.0
        else:
            if vd_sum > vd1:
                data_up += v
            if vd_sum < vd1:
                data_dn += v
        price_up = (not first) and c > pc
        price_dn = (not first) and c < pc
        ps = price_state
        if price_up:
            ps = 1
            p_up = v if price_state == -1 else p_up + v
            p_dn = 0.0
        elif price_dn:
            ps = -1
            p_dn = v if price_state == 1 else p_dn + v
            p_up = 0.0
        else:
            if price_state == -1:
                p_dn += v
            if price_state == 1:
                p_up += v
        diff_sum += (p_up - p_dn) + (data_up - data_dn)
        vd2 = vd1
        vd1 = vd_sum
        price_state = ps

        # ---- 7G Order Speed ----
        if ps == 1:
            up_time += dt
        else:
            dn_time += dt

        # ---- 7H Price Inertia ----
        dm = c if (first or c < pc) else dn_mem
        um = c if (first or c > pc) else up_mem
        if first:
            ds = v
            us = v
        else:
            if c != dm:
                ds = max(down_sum - v, 0.0)
            elif dm != dn_mem:
                ds = v
            else:
                ds = down_sum + v
            if c != um:
                us = max(up_sum - v, 0.0)
            elif um != up_mem:
                us = v
            else:
                us = up_sum + v
        ud = us - ds
        if first:
            order_diff = ud
        elif pi_reverse and dt != 0:
            order_diff -= ud
        else:
            order_diff += ud

        # ---- Referenz: Cumulative Delta ----
        delta += sd * v

        # ---- Zustand ----
        range_hi = hi
        range_lo = lo
        range_mean = rm
        range_value_sum = rvs
        range_vol_sum = rvol
        range_vwap = rvwap
        vwap_diff = vd
        break_effect = be
        box_mean = bm
        vwap_vol_sum = vvs
        vwap_price_sum = vps
        vwap1 = vw1
        vwap_bar_sum = vbs
        dn_mem = dm
        up_mem = um
        down_sum = ds
        up_sum = us
        pc = c
        pt = t
        pday = day

        out[i, 0] = be + vd
        out[i, 1] = vbs
        out[i, 2] = oa_sum
        out[i, 3] = sd_sum
        out[i, 4] = diff_sum + vd_sum
        out[i, 5] = market_orders
        out[i, 6] = up_time - dn_time
        out[i, 7] = order_diff
        out[i, 8] = delta
    return out


def compute(price, size, ts_ms, side_flag=None, side_mode="tick", mintick=0.25,
            rsi_len=50, sd_cycle=100, sd_sum_len=300, pi_reverse=True):
    """Komfort-Wrapper. Gibt (matrix, side) zurueck."""
    price = np.ascontiguousarray(price, dtype=np.float64)
    size = np.ascontiguousarray(size, dtype=np.float64)
    ts_ms = np.ascontiguousarray(ts_ms, dtype=np.int64)
    if side_mode == "flag":
        if side_flag is None:
            raise ValueError("side_mode='flag' braucht side_flag")
        side = np.ascontiguousarray(side_flag, dtype=np.int8)
    else:
        side = tick_rule_side(price)
    mat = run_algos(price, size, side, ts_ms, float(mintick), int(rsi_len),
                    int(sd_cycle), int(sd_sum_len), bool(pi_reverse))
    return mat, side
