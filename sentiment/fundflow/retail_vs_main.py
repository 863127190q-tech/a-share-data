#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C2 散户-主力背离判读(核心产物)——他"聪明钱 vs 散户分歧"仪器的A股版。
读 individual_flow.csv,算每日每票:散户方向=sign(小单净额) vs 主力方向=sign(主力净额)。
背离类型:
  吸筹(潜在底部)= 散户卖 且 主力买(散户净流出、主力净流入)
  派发(潜在顶部)= 散户买 且 主力卖
  共振买/共振卖 = 同向;中性 = 有一方≈0
产物: sentiment/fundflow/divergence_daily.csv
  列: date, code, name, 散户方向, 主力方向, 背离类型, 主力净额(元)
边界:分档是按单笔金额推断(超大单≈机构、小单≈散户),非真实席位,作方向性参考,不作精确归因。
"""
import datetime as dt
from pathlib import Path

import pandas as pd

FF = Path(__file__).resolve().parent
SENT = FF.parent
SRC = FF / "individual_flow.csv"
OUT = FF / "divergence_daily.csv"
CST = dt.timezone(dt.timedelta(hours=8))


def status(source, line):
    p = SENT / "_status.txt"
    now = dt.datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    rows = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if " | " in ln:
                rows[ln.split(" | ", 1)[0]] = ln
    rows[source] = f"{source} | {now} | {line}"
    p.write_text("\n".join(rows[k] for k in sorted(rows)) + "\n", encoding="utf-8")


def direction(v, eps=1e6):
    """净额→方向。eps=100万元阈值,小于视作中性(≈0)。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "?"
    if v > eps:
        return "买"
    if v < -eps:
        return "卖"
    return "平"


def classify(retail, main):
    if retail == "卖" and main == "买":
        return "吸筹(散户卖·主力买→潜在底部)"
    if retail == "买" and main == "卖":
        return "派发(散户买·主力卖→潜在顶部)"
    if retail == "买" and main == "买":
        return "共振买"
    if retail == "卖" and main == "卖":
        return "共振卖"
    return "中性"


def main():
    if not SRC.exists():
        status("散户主力背离", "SKIP individual_flow.csv 不存在(先跑fetch_fundflow,需国内本地)")
        print("SKIP: 无 individual_flow.csv")
        return
    df = pd.read_csv(SRC, dtype={"date": str, "code": str})
    if not len(df):
        status("散户主力背离", "SKIP individual_flow.csv 为空")
        return
    out = []
    for _, r in df.iterrows():
        retail = direction(r.get("小单净额"))
        mainv = direction(r.get("主力净额"))
        out.append([r["date"], str(r["code"]).zfill(6), r.get("name", ""),
                    retail, mainv, classify(retail, mainv), r.get("主力净额", "")])
    res = pd.DataFrame(out, columns=["date", "code", "name", "散户方向", "主力方向", "背离类型", "主力净额"])
    res = res.sort_values(["date", "code"]).reset_index(drop=True)
    OUT.write_text(res.to_csv(index=False), encoding="utf-8-sig")
    n_absorb = (res["背离类型"].str.startswith("吸筹")).sum()
    n_distrib = (res["背离类型"].str.startswith("派发")).sum()
    status("散户主力背离", f"OK {len(res)}行,吸筹{n_absorb}·派发{n_distrib} → divergence_daily.csv")
    print(f"divergence_daily.csv: {len(res)}行(吸筹{n_absorb}·派发{n_distrib})")


if __name__ == "__main__":
    main()
