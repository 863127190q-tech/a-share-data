#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日情绪报告(v3.1)。排版严格按优先级:哨兵(纲)→ 原话样本(证据)→ 民调计数/相变(底)→ navigator(导航)。
哨兵沉默也是读数(输出『今日哨兵无转向』,呼应研究对象的缺席记录法)。
用法: python sentiment/daily_report.py [YYYY-MM-DD]   → sentiment/reports/YYYYMMDD.md
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

SENT = Path("sentiment")
CST = dt.timezone(dt.timedelta(hours=8))

HEADER_NOTE = (
    "> **边界**:推特中文财经言论≠全体A股散户(偏出海、币股双栖);模型判读会错(尤其反讽),"
    "原话样本+人工抽查兜底;哨兵名单冷启动粗糙靠迭代;navigator是导航,原话是证据,哨兵是警报,"
    "读表的最后一下永远是人的活。")


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else dt.datetime.now(CST).date().isoformat()
    sys.path.insert(0, str(SENT))
    from regime import tag
    market, regime = tag(day)
    L = [f"# 散户情绪日报 {day}(市场:{market} 体制:{regime})", "", HEADER_NOTE, ""]

    # ① 哨兵层(纲)
    L.append("## ① 哨兵")
    ev = SENT / "sentinel" / "sentinel_events.csv"
    rows = pd.DataFrame()
    if ev.exists():
        e = pd.read_csv(ev, dtype={"date": str})
        rows = e[e["date"] == day]
    if len(rows):
        for _, r in rows.iterrows():
            L.append(f"- 🚨 **{r['handle']}**({r['type']})**{r['event']}**:{r['from_to']}"
                     f"(置信{r['confidence']}) [原文]({r['url']})")
    else:
        L.append("- 今日哨兵无转向(沉默也是读数——『还未看到批量躺赚推文』式的缺席记录)")
    L.append("")

    # ② 高权重原话样本(证据)
    L.append("## ② 原话样本(哨兵>大V>高互动>普通)")
    sf = SENT / "speech" / "speech_samples" / f"{day.replace('-', '')}.md"
    if sf.exists():
        body = sf.read_text(encoding="utf-8").split("\n", 4)
        L.append(body[4] if len(body) > 4 else "(样本文件为空)")
    else:
        L.append("(当日言论样本未生成——池未捞或未判读)")
    L.append("")

    # ③ 民调计数/相变(底)
    L.append("## ③ 民调计数与相变")
    sp = SENT / "speech" / "speech_daily.csv"
    if sp.exists():
        sd = pd.read_csv(sp, dtype={"date": str})
        sub = sd[(sd["date"] == day) & (sd["polarity"] != "中性")]
        if len(sub):
            L.append("| 极性 | 高置信条数 | 20日z | 相变 |")
            L.append("|---|---|---|---|")
            for _, r in sub.iterrows():
                flag = {"群体级": " 🔴" if r["polarity"] == "贪婪" else " 🟢"}.get(str(r["phase"]), "")
                L.append(f"| {r['polarity']} | {r['high_conf_count']} | {r['zscore_20d']} | {r['phase']}{flag} |")
        else:
            L.append("(当日无判读数据)")
    else:
        L.append("(speech_daily.csv 未生成)")
    L.append("")

    # ④ navigator(导航)
    L.append("## ④ navigator")
    rs = SENT / "retail_sentiment.csv"
    if rs.exists():
        r = pd.read_csv(rs, dtype={"date": str})
        row = r[r["date"] == day]
        if len(row):
            v = row.iloc[0]
            nav = v["navigator"]
            hist = r[r["navigator"].notna()]["navigator"]
            pct = int((hist < nav).mean() * 100) if pd.notna(nav) and len(hist) else "-"
            L.append(f"- navigator = **{nav:+.2f}**(历史第{pct}百分位,分量{int(v['n_分量'])}个,3日均{v['navigator_3d']:+.2f})")
        else:
            L.append("(当日不在序列内)")
    L.append("")
    out = SENT / "reports" / f"{day.replace('-', '')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
