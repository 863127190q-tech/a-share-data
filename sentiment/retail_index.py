#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
navigator 合成(v3.1,原composite更名) —— 导航,不是判决。
骨架=自然日连续(周末保留:言论分量照常,行情分量留空NaN);
分量=赚钱效应/涨停强度/两融变化/散户资金净流向/人气热度/言论贪恐差(共6,等权待迭代)。
哨兵层不入合成(权重原则:哨兵事件≈相变量级,日报单列)。
navigator 高=散户亢奋(潜在顶),低=冰点(潜在底);给相对高低与拐点,不给精确顶底。

只读 sentiment/ 与 data/;输出 retail_sentiment.csv(含market/regime环境标签)。
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

SENT = Path("sentiment")
OUT = SENT / "retail_sentiment.csv"
CST = dt.timezone(dt.timedelta(hours=8))


def zscore(s):
    s = pd.to_numeric(s, errors="coerce")
    sd = s.std(ddof=0)
    if not sd or pd.isna(sd):
        return s * 0.0
    return (s - s.mean()) / sd


def main():
    md = pd.read_csv(SENT / "market_daily.csv", dtype={"date": str})

    def series(source, metric):
        sub = md[(md["source"] == source) & (md["metric"] == metric)]
        return sub.set_index("date")["value"].apply(pd.to_numeric, errors="coerce")

    breadth = series("data-宇宙", "赚钱效应_涨跌比")
    zt = series("data-池", "涨停家数")
    margin = series("sse", "融资余额")
    retail_net = series("agg", "散户资金净流向")
    hot_rank = series("agg", "人气排名均值")

    # 言论层日度净读数:高置信(贪婪-恐惧-投降)条数差(speech_daily.csv,可含周末)
    speech_net = pd.Series(dtype=float)
    sp = SENT / "speech" / "speech_daily.csv"
    if sp.exists():
        sd = pd.read_csv(sp, dtype={"date": str})
        piv = sd.pivot_table(index="date", columns="polarity",
                             values="high_conf_count", aggfunc="sum").fillna(0)
        speech_net = (piv.get("贪婪", 0) - piv.get("恐惧", 0) - piv.get("投降", 0)).astype(float)

    # 骨架 = 自然日连续:从各源最早到今天(北京),周末保留
    all_idx = set(breadth.index) | set(margin.index) | set(zt.index) | set(speech_net.index)
    if not all_idx:
        print("无任何分量数据")
        return
    d0 = dt.date.fromisoformat(min(all_idx))
    d1 = max(dt.date.fromisoformat(max(all_idx)), dt.datetime.now(CST).date())
    dates = []
    d = d0
    while d <= d1:
        dates.append(d.isoformat())
        d += dt.timedelta(days=1)

    df = pd.DataFrame(index=dates)
    df.index.name = "date"
    df["赚钱效应"] = breadth.reindex(dates)          # 行情分量:非交易日自然为NaN(留空,不填)
    df["涨停强度"] = zt.reindex(dates)
    df["两融变化"] = margin.reindex(dates).astype(float).pct_change() * 100
    df["散户资金净流向"] = retail_net.reindex(dates)
    df["人气热度"] = (-hot_rank).reindex(dates)
    df["言论贪恐差"] = speech_net.reindex(dates)     # 言论分量:周末照常有值

    comp = pd.DataFrame({f"z_{c}": zscore(df[c]) for c in
                         ["赚钱效应", "涨停强度", "两融变化", "散户资金净流向", "人气热度", "言论贪恐差"]})
    df["navigator"] = comp.mean(axis=1, skipna=True)
    df["n_分量"] = comp.notna().sum(axis=1)
    df["navigator_3d"] = df["navigator"].rolling(3, min_periods=1).mean()

    # 环境标签(无标签不出厂)
    sys.path.insert(0, str(SENT))
    from regime import tag
    tags = [tag(d) for d in dates]
    df["market"] = [t[0] for t in tags]
    df["regime"] = [t[1] for t in tags]

    df = df.reset_index()
    cols = ["date", "散户资金净流向", "人气热度", "两融变化", "赚钱效应", "涨停强度",
            "言论贪恐差", "navigator", "navigator_3d", "n_分量", "market", "regime"]
    df[cols].to_csv(OUT, index=False, encoding="utf-8-sig")

    valid = df[df["navigator"].notna()]
    hi, lo = valid.loc[valid["navigator"].idxmax()], valid.loc[valid["navigator"].idxmin()]
    print(f"retail_sentiment.csv: 自然日{len(df)}行 {df['date'].min()}~{df['date'].max()}")
    print(f"navigator 最高: {hi['date']} {hi['navigator']:+.2f} | 最低: {lo['date']} {lo['navigator']:+.2f}")
    print("navigator是导航,原话是证据,哨兵是警报;权重等权待迭代")


if __name__ == "__main__":
    main()
