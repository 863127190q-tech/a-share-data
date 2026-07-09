#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 TingHu 校准散户情绪指数 —— 情绪框架 v2 中推文的【正确用途】
TingHu 推文不是情绪源,是【观察者读数/标准答案】:用他明确表达"情绪高该止盈"与
"情绪冰点该抄底"的时点,去检验 retail_sentiment.csv 的极值是否与之吻合。
吻合→指数可用;不吻合→如实报告,提示调权重/换指标——这正是校准的意义。

只读 sentiment/retail_sentiment.csv 与 tweets.jsonl(种子日期已人工锚定,附原文url)。
输出 sentiment/calibration.md。
"""
from pathlib import Path

import pandas as pd

SENT = Path("sentiment")
OUT = SENT / "calibration.md"

# 种子时点(人工锚定,原文url在 tinghu/tweets.jsonl 可核)
SEEDS = [
    {
        "kind": "底(情绪冰点/该抄底)", "expect": "low",
        "window": ("2026-06-08", "2026-06-11"),
        "desc": "6月中旬SpaceX吸血杀跌,他连续盲捞/捞油水抄底;06-12复盘确认'基本就是最低位,股票情绪恐慌依旧有效'",
        "anchors": [
            ("2026-06-10", "今夜还继续大跌就是相对确定的捞油水机会", "https://x.com/TingHu888/status/2064593905082056895"),
            ("2026-06-12", "本周A美捞油水的位置都还不错,基本就是最低位", "https://x.com/TingHu888/status/2065373545698668608"),
        ],
    },
    {
        "kind": "顶(情绪亢奋/该止盈)", "expect": "high",
        "window": ("2026-06-30", "2026-07-01"),
        "desc": "长鑫存储IPO前存储FOMO,他06-24起明牌减仓、06-25密集讲止盈,06-30/07-01'去弱留强整体减仓',07-02自认'减仓在了高位'",
        "anchors": [
            ("2026-06-25", "正式平衡之前的预期平衡就是一个比较好的止盈点", "https://x.com/TingHu888/status/2070088662327669003"),
            ("2026-07-01", "今天的去弱留强整体减仓还不够", "https://x.com/TingHu888/status/2072327348079534429"),
            ("2026-07-02", "竟然还是靠运气减仓在了高位", "https://x.com/TingHu888/status/2072520410604974333"),
        ],
    },
]


def pct_rank(series, value):
    """value 在 series 中的百分位(0=最低,100=最高)。"""
    s = series.dropna()
    if not len(s) or pd.isna(value):
        return None
    return round((s < value).sum() / len(s) * 100, 0)


def main():
    df = pd.read_csv(SENT / "retail_sentiment.csv", dtype={"date": str})
    comp = df.set_index("date")["composite"]
    comp3 = df.set_index("date")["composite_3d"]
    dmin, dmax = df["date"].min(), df["date"].max()
    lo_row = df.loc[comp.reset_index(drop=True).idxmin()]
    hi_row = df.loc[comp.reset_index(drop=True).idxmax()]

    L = []
    L.append("# 散户情绪指数 × TingHu 校准报告(草稿·未审计)")
    L.append("")
    L.append("> **方法**:TingHu 推文在此是**标尺/标准答案**,不是情绪源。用他明确的顶/底判断时点,"
             "检验 `retail_sentiment.csv` 的 composite 极值是否吻合。composite 高=散户亢奋(潜在顶),低=冰点(潜在底)。")
    L.append(f"> **区间**:{dmin} ~ {dmax}(共{len(df)}交易日) · composite 全局最低 {lo_row['date']}"
             f"({lo_row['composite']:+.2f})、最高 {hi_row['date']}({hi_row['composite']:+.2f})")
    L.append("")

    verdicts = []
    for seed in SEEDS:
        w0, w1 = seed["window"]
        win = df[(df["date"] >= w0) & (df["date"] <= w1)]
        L.append(f"## {seed['kind']}")
        L.append("")
        L.append(f"- **TingHu判断**:{seed['desc']}")
        L.append(f"- **种子窗口**:{w0} ~ {w1}")
        L.append("- **锚点原文**:")
        for d, txt, url in seed["anchors"]:
            L.append(f"  - [{d}] “{txt}” — {url}")
        L.append("")
        L.append("| 日期 | composite | 3日平滑 | 全序列百分位 |")
        L.append("|---|---|---|---|")
        vals = []
        for _, r in win.iterrows():
            p = pct_rank(comp, r["composite"])
            vals.append((r["date"], r["composite"], p))
            L.append(f"| {r['date']} | {r['composite']:+.2f} | {r['composite_3d']:+.2f} | {p:.0f}% |")
        L.append("")
        # 判定:底→窗口内最低应落低位(百分位≤33);顶→窗口内最高应落高位(≥67)
        if seed["expect"] == "low":
            best = min(vals, key=lambda x: x[1])
            ok = best[2] is not None and best[2] <= 33
            verdicts.append((seed["kind"], ok, f"窗口最冰点 {best[0]} 处全序列第{best[2]:.0f}百分位"
                             + ("(低位,吻合✓)" if ok else "(未落低位,偏差)")))
        else:
            best = max(vals, key=lambda x: x[1])
            ok = best[2] is not None and best[2] >= 67
            verdicts.append((seed["kind"], ok, f"窗口最亢奋 {best[0]} 处全序列第{best[2]:.0f}百分位"
                             + ("(高位,吻合✓)" if ok else "(未落高位,偏差)")))
        L.append(f"**吻合判定**:{verdicts[-1][2]}")
        L.append("")

    L.append("## 结论与迭代方向")
    L.append("")
    for kind, ok, msg in verdicts:
        L.append(f"- {'✅' if ok else '⚠️'} **{kind}**:{msg}")
    L.append("")
    L.append("**已知局限(决定下一步迭代)**:")
    L.append("1. composite 现仅由【赚钱效应(结算宇宙breadth)+涨停强度+两融变化】等权合成;"
             "**散户资金净流向、人气热度两个核心代理在历史区间缺位**(个股资金流走push2his海外被墙;"
             "东财人气榜/雪球关注仅当天快照无历史)。日常滚动在国内出口补齐后,顶部识别力应显著改善。")
    L.append("2. breadth 用的是结算宇宙(330只、偏AI/存储),对**大盘级**冰点灵敏、对**存储单板块**FOMO顶"
             "会被大盘稀释;可迭代为按题材(存储链)细分的breadth。")
    L.append("3. 等权权重为占位,`待迭代`;校准暴露的偏差正是调权/换指标的依据。")
    L.append("")
    L.append("> 情绪指数给的是**相对高低与拐点**,不是精确顶底;最后判断仍是人的活。")

    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"calibration.md 写出;判定:", "; ".join(f"{k}{'✓' if ok else '✗'}" for k, ok, _ in verdicts))


if __name__ == "__main__":
    main()
