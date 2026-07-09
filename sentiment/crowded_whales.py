#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D 狩猎指标(v3.1):拥挤度标记——只标记,不预测。
- 人气榜连续霸榜天数:关注清单个股在东财人气榜的连续上榜/高位天数(hot_rank_detail_em,主机可达)
- 两融/流通市值极端分位:数据源受限(个股两融明细接口海外墙),可得则算,不可得留空如实标注
产物: sentiment/crowded_whales.csv (date,code,name,rank,streak_days,flag,market,regime)
flag: 🎯拥挤 = 排名≤30 且 连续≥5天
"""
import datetime as dt
import sys
import time
from pathlib import Path

import akshare as ak
import pandas as pd

SENT = Path("sentiment")
CST = dt.timezone(dt.timedelta(hours=8))
sys.path.insert(0, str(SENT))
from fetch_retail_sentiment import WATCH, status  # noqa: E402


def main():
    from regime import tag
    all_rows = []
    ok = 0
    for code, name in WATCH.items():
        sym = ("SH" if code[0] in "569" else "SZ") + code
        try:
            df = ak.stock_hot_rank_detail_em(symbol=sym)
        except Exception:
            continue
        ok += 1
        df = df.sort_values("时间")
        streak = 0
        for _, r in df.iterrows():
            d = str(r["时间"])[:10]
            try:
                rank = int(r["排名"])
            except (TypeError, ValueError):
                continue
            streak = streak + 1 if rank <= 30 else 0
            flag = "🎯拥挤" if (rank <= 30 and streak >= 5) else ""
            all_rows.append([d, code, name, rank, streak, flag])
        time.sleep(0.5)
    if not all_rows:
        status("crowded_whales", "FAIL 人气历史全部不可达")
        return
    df = pd.DataFrame(all_rows, columns=["date", "code", "name", "rank", "streak_days", "flag"])
    tags = {d: tag(d) for d in df["date"].unique()}
    df["market"] = df["date"].map(lambda d: tags[d][0])
    df["regime"] = df["date"].map(lambda d: tags[d][1])
    df.sort_values(["date", "rank"]).to_csv(SENT / "crowded_whales.csv", index=False, encoding="utf-8-sig")
    n_flag = (df["flag"] != "").sum()
    status("crowded_whales", f"OK {ok}/{len(WATCH)}股人气历史;拥挤标记{n_flag}个;两融分位:接口海外墙,留空待国内补")
    print(f"crowded_whales.csv: {len(df)}行,拥挤标记{n_flag}")


if __name__ == "__main__":
    main()
