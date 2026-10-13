"""주 분석: 완전성·최신성·일관성·과밀/여유·수용불가 메시지·시도별 비교.

실행 예:
    python analysis/quality.py                      (전국, 기본 기간)
    python analysis/quality.py --sido 서울특별시 --start "2026-09-22 14:50" --end "2026-10-06"
결과: analysis/out/ 에 CSV, 요약은 표준출력.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from common import CL, SEVERE_COLS, SH, api, outpath, parse_args, prep, wilson

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 300)


def q(s: pd.Series):
    return (round(s.median(), 1), round(s.quantile(.25), 1), round(s.quantile(.75), 1), round(s.quantile(.9), 1), round(s.max()))


def reason(s: str) -> str:
    if s.strip() in ("불가능메세지등록", "불가능메시지등록", ".", ""):
        return "사유 미기재"
    if re.search("의료진|인력|전문의|부재|당직|휴가|학회|사직|공백|인원", s):
        return "인력"
    if re.search("병상|만실|포화|과밀|중환자실|ICU|입원실|격리|베드|bed", s, re.I):
        return "병상"
    if re.search("장비|고장|수리|점검|기기|CT|MRI|angio|혈관조영", s, re.I):
        return "장비"
    if re.search(r"시간|평일|주말|야간|공휴일|휴일|요일|\d{1,2}:\d{2}|\d시", s):
        return "시간·요일 제한"
    if re.search("수술|시술", s):
        return "수술·시술 불가"
    return "기타"


def main():
    a = parse_args("응급의료 종합상황판 공개 데이터 품질 분석")
    B, V, M = prep("beds", a), prep("severe", a), prep("messages", a)
    print("종별(병상)", B.drop_duplicates("hpid").emcls_name.value_counts().to_dict())
    print("종별(수용가능)", V.drop_duplicates("hpid").emcls_name.value_counts().to_dict())
    b = B[B.emcls_name.isin(CL)].copy()
    v = V[V.emcls_name.isin(CL) & V.hpid.isin(b.hpid)].copy()
    ms = M[M.hpid.isin(b.hpid)].copy()
    R = b.rt.nunique()
    print("회차", R, b.rt.min(), b.rt.max(), "기관", b.hpid.nunique(), "관측", len(b), "수용가능 관측", len(v), "기관", v.hpid.nunique())
    exp = R * b.hpid.nunique()
    print("병상 결측 기관-회차", exp - len(b), round(100 * (exp - len(b)) / exp, 2))

    # ── 완전성 ──────────────────────────────────────────────
    L = v.melt(id_vars=["collected_at", "hpid", "emcls_name", "sido"], value_vars=SEVERE_COLS, var_name="ty", value_name="val")
    L["n"] = L.ty.str[8:].astype(int)
    L["miss"] = L.val.eq("정보미제공")
    print("=== 완전성")
    print("전체", wilson(L.miss.sum(), len(L)), "분류-기관-시점", len(L))
    t = L.groupby("n").miss.mean().mul(100).round(1)
    print("분류별 범위", t.min(), t.max(), api.SEVERE_TYPES[t.idxmin()], api.SEVERE_TYPES[t.idxmax()])
    for c in CL:
        x = L[L.emcls_name == c]
        print(SH[c], wilson(x.miss.sum(), len(x)))
    inst = L.groupby(["hpid", "emcls_name"]).miss.mean()
    print("전부 미제공 기관", (inst == 1).groupby("emcls_name").sum().to_dict(), "/", inst.groupby("emcls_name").size().to_dict())
    pv = L.pivot_table(index="n", columns="emcls_name", values="miss", aggfunc="mean").mul(100).round(1)
    pv.index = [api.SEVERE_TYPES[i] for i in pv.index]
    pv[CL].to_csv(outpath(a, "히트맵_미입력.csv"), encoding="utf-8-sig")
    print(pv[CL])
    sd = L.groupby("sido").miss.mean().mul(100).round(1).sort_values()
    print("시도별", sd.to_dict())

    # ── 최신성 ──────────────────────────────────────────────
    print("=== 최신성")
    b["lag"] = (b.t - pd.to_datetime(b.hvidate, format="%Y%m%d%H%M%S", errors="coerce")).dt.total_seconds() / 60
    print("lag NA", b.lag.isna().sum(), "음수(서버 시계 차이)", (b.lag < 0).sum())
    print("전체 med/q1/q3/p90/max", q(b.lag))
    for th in (15, 60, 480, 1440):
        print(f">{th}분", "전체", wilson((b.lag > th).sum(), len(b)), {SH[c]: wilson((b[b.emcls_name == c].lag > th).sum(), (b.emcls_name == c).sum()) for c in CL})
    for c in CL:
        print(SH[c], q(b[b.emcls_name == c].lag))
    il = b.groupby("hpid").lag.median()
    print("기관 중앙값 >15분", (il > 15).sum(), ">60분", (il > 60).sum())
    vs = v.sort_values(["hpid", "t"])
    vs["sig"] = vs[SEVERE_COLS].agg("|".join, axis=1)
    ch = vs.groupby(["hpid", "emcls_name"]).sig.apply(lambda s: (s != s.shift()).sum() - 1)
    print("수용가능 값 변경 0회 기관", (ch == 0).sum(), "/", len(ch), round(100 * (ch == 0).mean(), 1), (ch == 0).groupby("emcls_name").sum().to_dict(), "중앙값 변경횟수", ch.median())

    # ── 일관성 ──────────────────────────────────────────────
    print("=== 일관성")
    ms["st"] = pd.to_datetime(ms.symBlkSttDtm, format="%Y%m%d%H%M%S", errors="coerce")
    ms["en"] = pd.to_datetime(ms.symBlkEndDtm, format="%Y%m%d%H%M%S", errors="coerce")
    sm = ms[ms.symBlkMsgTyp == "중증"].copy()
    sm["n"] = sm.symTypCod.map(api.SYMTYP_TO_SEVERE)
    k = sm.drop_duplicates(["collected_at", "hpid", "n"])[["collected_at", "hpid", "n"]].merge(
        L[["collected_at", "hpid", "n", "val", "emcls_name"]], on=["collected_at", "hpid", "n"], how="inner")
    print("메시지 게시 기관-분류-시점", len(k), k.val.value_counts().to_dict(),
          "Y비율", wilson((k.val == "Y").sum(), len(k)), "미제공비율", wilson((k.val == "정보미제공").sum(), len(k)))
    c = k[k.val == "Y"].drop_duplicates(["collected_at", "hpid"])
    print("모순≥1 기관-시점", wilson(len(c), len(v)), "기관수", c.hpid.nunique(),
          {SH[x]: wilson((c.emcls_name == x).sum(), (v.emcls_name == x).sum()) for x in CL})

    # ── 과밀·여유 ───────────────────────────────────────────
    print("=== 과밀·여유 (응급실 일반병상 hvec / 기준 hvs01)")
    b["a"] = pd.to_numeric(b.hvec, errors="coerce")
    b["base"] = pd.to_numeric(b.hvs01, errors="coerce")
    over = b.a < 0
    half = (b.a >= 0.5 * b.base) & (b.base > 0)
    print("과밀 전체", wilson(over.sum(), len(b)), {SH[c]: wilson((over & (b.emcls_name == c)).sum(), (b.emcls_name == c).sum()) for c in CL})
    print("절반 이상 여유 전체", wilson(half.sum(), len(b)), {SH[c]: wilson((half & (b.emcls_name == c)).sum(), (b.emcls_name == c).sum()) for c in CL})
    print("가용>0", {SH[c]: round(100 * ((b.a > 0) & (b.emcls_name == c)).sum() / (b.emcls_name == c).sum(), 1) for c in CL})
    print("가용>기준(이상치)", int(((b.a > b.base) & b.base.notna()).sum()))

    # ── 메시지 ──────────────────────────────────────────────
    print("=== 수용불가 메시지")
    print("게시 기관", ms.hpid.nunique(), "/", b.hpid.nunique(), ms.drop_duplicates("hpid").emcls_name.value_counts().to_dict(),
          "종별 기관수", b.drop_duplicates("hpid").emcls_name.value_counts().to_dict())
    u = ms.groupby(["hpid", "emcls_name", "symBlkMsgTyp", "symTypCod", "symBlkMsg", "symBlkSttDtm"]).agg(en=("en", "max"), st=("st", "first")).reset_index()
    u["dur_h"] = (u.en - u.st).dt.total_seconds() / 3600
    print("고유 메시지", len(u), u.symBlkMsgTyp.value_counts().to_dict(), "게시기간 h med/q1/q3", q(u.dur_h)[:3],
          "≥7일", wilson((u.dur_h >= 167.9).sum(), len(u)))
    base_msg = u.symBlkMsg.str.strip().isin(["불가능메세지등록", "불가능메시지등록", ".", ""])
    print("중증 메시지 기본문구", wilson((base_msg & (u.symBlkMsgTyp == "중증")).sum(), (u.symBlkMsgTyp == "중증").sum()))
    print("사유(키워드 분류, %)", (u.symBlkMsg.map(reason).value_counts(normalize=True) * 100).round(1).to_dict())
    u.to_csv(outpath(a, "고유메시지.csv"), index=False, encoding="utf-8-sig")

    # ── 시도별 표 ───────────────────────────────────────────
    g = b.groupby("sido").agg(기관=("hpid", "nunique"), 시차중앙=("lag", "median"),
                             초과60=("lag", lambda s: round(100 * (s > 60).mean(), 2)),
                             과밀=("a", lambda s: round(100 * (s < 0).mean(), 1)))
    g["미입력"] = sd
    g = g.sort_values("미입력")
    print(g)
    g.to_csv(outpath(a, "시도별.csv"), encoding="utf-8-sig")
    b[["hpid", "emcls_name", "sido", "t", "lag", "a", "base"]].to_pickle(outpath(a, "beds_slim.pkl"))
    print("완료:", a.out)


if __name__ == "__main__":
    main()
