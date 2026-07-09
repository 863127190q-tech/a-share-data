#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合成散户情绪指数 —— 情绪框架 v2
把 sentiment/ 下多源归一化合成一条曲线 sentiment/retail_sentiment.csv。
composite 高 = 散户亢奋(潜在顶),低 = 散户冰点(潜在底)。

只读 sentiment/ 与 data/(经fetch派生的long表),不触碰任何现有行情/推文文件。
权重先用等权,标注"待迭代";海外被墙、当前只有快照无历史的源(散户资金净流向、
人气热度),在历史区间列为NaN并如实标注,不用0或均值伪填。

用法: python sentiment/retail_index.py
"""
from pathlib import Path

import pandas as pd

SENT = Path("sentiment")
OUT = SENT / "retail_sentiment.csv"


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

    breadth = series("data-宇宙", "赚钱效应_涨跌比")     # 赚钱效应(全区间)
    zt = series("data-池", "涨停家数")                   # 涨停强度(仅06-18+)
    margin = series("sse", "融资余额")                   # 两融余额(全区间)

    dates = sorted(breadth.index.union(margin.index).union(zt.index))
    df = pd.DataFrame(index=dates)
    df.index.name = "date"

    # 各代理原值
    df["赚钱效应"] = breadth
    df["涨停强度"] = zt
    df["两融变化"] = margin.reindex(dates).astype(float).pct_change() * 100  # 日环比%
    # 海外被墙 / 仅快照无历史 → 历史区间置NaN,如实标注(不伪填)
    df["散户资金净流向"] = pd.NA   # individual_fund_flow 海外push2his被墙
    df["人气热度"] = pd.NA         # hot_rank/雪球 仅当天快照,无历史;日常滚动起逐步有值

    # 归一化(z-score)后等权合成,每日按"当日可用分量"求均值
    comp_parts = pd.DataFrame({
        "z_赚钱效应": zscore(df["赚钱效应"]),
        "z_涨停强度": zscore(df["涨停强度"]),
        "z_两融变化": zscore(df["两融变化"]),
    })
    df["composite"] = comp_parts.mean(axis=1, skipna=True)          # 等权(待迭代)
    df["n_分量"] = comp_parts.notna().sum(axis=1)
    df["composite_3d"] = df["composite"].rolling(3, min_periods=1).mean()  # 3日平滑

    df = df.reset_index()
    cols = ["date", "散户资金净流向", "人气热度", "两融变化", "赚钱效应",
            "涨停强度", "composite", "composite_3d", "n_分量"]
    df[cols].to_csv(OUT, index=False, encoding="utf-8-sig")

    lo = df.loc[df["composite"].idxmin()]
    hi = df.loc[df["composite"].idxmax()]
    print(f"retail_sentiment.csv: {len(df)}日  {df['date'].min()}~{df['date'].max()}")
    print(f"composite 最高(最亢奋): {hi['date']}  {hi['composite']:.2f}")
    print(f"composite 最低(最冰点): {lo['date']}  {lo['composite']:.2f}")
    print("注:散户资金净流向/人气热度 历史区间NaN(海外被墙/仅快照),日常滚动起补;权重等权待迭代")


if __name__ == "__main__":
    main()
