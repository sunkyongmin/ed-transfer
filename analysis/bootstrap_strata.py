"""보완 분석: 기관 군집 부트스트랩 신뢰구간, 평일·휴일 × 주간·야간 층화, 센터급 핵심 분류 민감도.

실행 예:
    python analysis/bootstrap_strata.py --sido 서울특별시 --start "2026-09-22 14:50" --end "2026-10-06" --tag seoul
    python analysis/bootstrap_strata.py --tag nat
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import CL, SEVERE_COLS, SH, api, outpath, parse_args, prep, wilson

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 200)

HOLIDAYS = {"2026-09-24", "2026-09-25", "2026-09-26", "2026-10-05", "2026-10-09"}  # 추석 연휴, 개천절 대체, 한글날
CORE = {1: "심근경색 재관류", 2: "뇌경색 재관류", 3: "거미막하출혈 수술", 4: "거미막하출혈 외 수술", 9: "복부응급수술", 5: "대동맥 흉부", 6: "대동맥 복부"}


def add_strata(d: pd.DataFrame) -> pd.DataFrame:
    day = d.t.dt.normalize()
    d["holiday"] = (d.t.dt.weekday >= 5) | day.isin(pd.to_datetime(sorted(HOLIDAYS)))
    d["night"] = ~d.t.dt.hour.between(8, 17)
    d["stratum"] = np.where(d.holiday, "휴일", "평일") + " " + np.where(d.night, "야간", "주간(08–18시)")
    return d


def cluster_boot(df: pd.DataFrame, col: str, rng, B: int):
    g = df.groupby("hpid")[col].agg(["sum", "count"])
    s, c = g["sum"].values, g["count"].values
    est = s.sum() / c.sum()
    n = len(g)
    out = np.empty(B)
    for i in range(B):
        idx = rng.integers(0, n, n)
        out[i] = s[idx].sum() / c[idx].sum()
    return round(100 * est, 1), round(100 * np.percentile(out, 2.5), 1), round(100 * np.percentile(out, 97.5), 1), n


def main():
    a = parse_args("군집 부트스트랩·층화 분석", lambda ap: (ap.add_argument("--B", type=int, default=2000), ap.add_argument("--seed", type=int, default=20261006)))
    B_, V_, M_ = prep("beds", a), prep("severe", a), prep("messages", a)
    b = add_strata(B_[B_.emcls_name.isin(CL)].copy())
    v = add_strata(V_[V_.emcls_name.isin(CL) & V_.hpid.isin(b.hpid)].copy())
    ms = M_[M_.hpid.isin(b.hpid)].copy()
    v["miss"] = v[SEVERE_COLS].eq("정보미제공").mean(axis=1)
    b["lag"] = (b.t - pd.to_datetime(b.hvidate, format="%Y%m%d%H%M%S", errors="coerce")).dt.total_seconds() / 60
    b["a"] = pd.to_numeric(b.hvec, errors="coerce")
    b["base"] = pd.to_numeric(b.hvs01, errors="coerce")
    b["over"] = b.a < 0
    b["halffree"] = (b.a >= 0.5 * b.base) & (b.base > 0)
    b["lag15"] = b.lag > 15
    b["lag60"] = b.lag > 60
    sm = ms[ms.symBlkMsgTyp == "중증"].copy()
    sm["n"] = sm.symTypCod.map(api.SYMTYP_TO_SEVERE)
    L = v.melt(id_vars=["collected_at", "hpid", "emcls_name"], value_vars=SEVERE_COLS, var_name="ty", value_name="val")
    L["n"] = L.ty.str[8:].astype(int)
    k = sm.drop_duplicates(["collected_at", "hpid", "n"])[["collected_at", "hpid", "n"]].merge(L, on=["collected_at", "hpid", "n"])
    contra = k[k.val == "Y"].drop_duplicates(["collected_at", "hpid"])[["collected_at", "hpid"]].assign(c=True)
    v = v.merge(contra, on=["collected_at", "hpid"], how="left")
    v["c"] = v.c.eq(True)
    inst = b.drop_duplicates("hpid")[["hpid", "emcls_name"]].assign(posted=lambda d: d.hpid.isin(ms.hpid))

    rng = np.random.default_rng(a.seed)
    rows = []

    def add(name, df, col, tier="전체"):
        e, lo, hi, n = cluster_boot(df, col, rng, a.B)
        w = wilson(df[col].sum(), len(df))
        rows.append(dict(지표=name, 종별=tier, 추정치=e, 군집부트스트랩_하한=lo, 군집부트스트랩_상한=hi, Wilson_하한=w[1], Wilson_상한=w[2], 기관수=n, 관측치=len(df)))

    for name, df, col in [("수용가능 미입력", v, "miss"), ("병상 시차 >15분", b, "lag15"), ("병상 시차 >60분", b, "lag60"),
                          ("Y+수용불가 메시지 동시", v, "c"), ("응급실 과밀(가용<0)", b, "over"), ("기준병상 절반 이상 여유", b, "halffree")]:
        add(name, df, col)
        for c in CL:
            add(name, df[df.emcls_name == c], col, SH[c])
    for c in CL:
        x = inst[inst.emcls_name == c]
        w = wilson(x.posted.sum(), len(x))
        rows.append(dict(지표="수용불가 메시지 게시 기관(기관 단위)", 종별=SH[c], 추정치=w[0], 군집부트스트랩_하한=np.nan, 군집부트스트랩_상한=np.nan, Wilson_하한=w[1], Wilson_상한=w[2], 기관수=len(x), 관측치=len(x)))
    R = pd.DataFrame(rows)
    print(R.to_string())
    R.to_csv(outpath(a, "표_군집부트스트랩.csv"), index=False, encoding="utf-8-sig")

    print("\n=== 층화: 평일/휴일 × 주간/야간 (휴일 =", sorted(HOLIDAYS), "+ 토·일)")
    s1 = v.groupby("stratum")[["miss", "c"]].mean().mul(100).round(2)
    s2 = b.groupby("stratum")[["lag15", "lag60", "over", "halffree"]].mean().mul(100).round(1)
    s2["관측치"] = b.groupby("stratum").size()
    S = pd.concat([s1, s2], axis=1)
    print(S)
    S.to_csv(outpath(a, "표_층화.csv"), encoding="utf-8-sig")
    for name, df, col in [("미입력", v, "miss"), ("과밀", b, "over")]:
        t = df.groupby(["stratum", "emcls_name"])[col].mean().mul(100).round(1).unstack()[CL]
        print(name)
        print(t)
        t.to_csv(outpath(a, f"표_층화_{name}_종별.csv"), encoding="utf-8-sig")
    hour = b.assign(h=b.t.dt.hour).groupby(["holiday", "emcls_name", "h"]).over.mean().mul(100).reset_index()
    hour.to_csv(outpath(a, "과밀_시간대.csv"), index=False)

    print("\n=== 센터급 핵심 분류 미입력(%)")
    pv = L[L.n.isin(CORE)].groupby(["n", "emcls_name"]).val.apply(lambda s: round(100 * (s == "정보미제공").mean(), 1)).unstack()
    pv.index = [CORE[i] for i in pv.index]
    print(pv[CL])
    pv[CL].to_csv(outpath(a, "표_핵심분류_미입력.csv"), encoding="utf-8-sig")
    b[["hpid", "emcls_name", "t", "lag", "a", "base", "over", "stratum"]].to_pickle(outpath(a, "beds_strata.pkl"))
    print("완료:", a.out)


if __name__ == "__main__":
    main()
