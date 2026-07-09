#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
环境标签器(v3.1 共用):对任一自然日给出 market + regime 粗判。
regime 判据(自动,判不了填"未定",禁猜):
  - 趋势:结算宇宙等权均价的20日方向(±1.5%阈值)
  - 赚钱效应:market_daily.csv 的 赚钱效应_涨跌比 当日值
    牛=趋势升&赚钱>55 | 熊=趋势降&赚钱<45 | 退潮=趋势升但赚钱<45(高位钝化)
    修复=趋势降但赚钱>55(超跌反抽) | 震荡=其余有数据情形 | 未定=数据不足
market 恒为 "A股"(本框架只测A股散户;言论层里币圈/美股话题由判读层单独标)。
"""
import datetime as dt
from functools import lru_cache
from pathlib import Path

import pandas as pd

SENT = Path(__file__).resolve().parent
REPO = SENT.parent


@lru_cache(maxsize=1)
def _breadth():
    p = SENT / "market_daily.csv"
    if not p.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(p, dtype={"date": str})
    b = df[df["metric"] == "赚钱效应_涨跌比"].set_index("date")["value"]
    return pd.to_numeric(b, errors="coerce")


@lru_cache(maxsize=1)
def _universe_close_mean():
    """结算宇宙等权收盘均值序列(近似指数,用于20日趋势)。"""
    rows = {}
    for d in sorted(REPO.glob("data/2026*")):
        p = d / "hist_universe.csv"
        col = "收盘"
        if not p.exists():
            p = d / "all_stocks.csv"
            col = "最新价"
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p, encoding="utf-8-sig")
            v = pd.to_numeric(df[col], errors="coerce").mean()
            if pd.notna(v):
                iso = f"{d.name[:4]}-{d.name[4:6]}-{d.name[6:]}"
                rows[iso] = float(v)
        except Exception:
            continue
    return pd.Series(rows).sort_index()


def tag(date_iso):
    """→ (market, regime)。regime 判不出=未定。"""
    closes = _universe_close_mean()
    breadth = _breadth()
    trend = None
    past = closes[closes.index <= date_iso]
    if len(past) >= 21:
        chg = past.iloc[-1] / past.iloc[-21] - 1
        trend = "up" if chg > 0.015 else ("down" if chg < -0.015 else "flat")
    b = breadth.get(date_iso)
    if trend is None or b is None or pd.isna(b):
        return "A股", "未定"
    if trend == "up" and b > 55:
        return "A股", "牛"
    if trend == "down" and b < 45:
        return "A股", "熊"
    if trend == "up" and b < 45:
        return "A股", "退潮"
    if trend == "down" and b > 55:
        return "A股", "修复"
    return "A股", "震荡"


if __name__ == "__main__":
    for d in ["2026-06-08", "2026-06-17", "2026-06-30", "2026-07-02"]:
        print(d, tag(d))
