"""
응급의료 종합상황판 공개 데이터 품질 분석 (연구 A, 인간대상연구 아님).

snapshot_collector.py 가 쌓은 snapshots/beds_*.csv, severe_*.csv, messages_*.csv 를 읽어
완전성·최신성·일관성 지표를 산출하고, 초록에 옮길 요약을 출력한다.

사용법 (ed-transfer 폴더, 가상환경 켠 상태)
    python analyze_snapshots.py                                   # 서울, 9/22~10/5 (초록용 기본값)
    python analyze_snapshots.py --start 2026-09-24T17:00 --national  # 전국 시도 비교 추가
    python analyze_snapshots.py --sido 경기도 --end 2026-10-05T23:59

산출: analysis_out/ 폴더 (summary.md + 표 CSV)

지표 정의 (계획서 4.5 대응, 실제 API 구조에 맞춤)
  완전성   중증질환 27개 분류 값이 '정보미제공' 또는 빈칸인 기관-시점 비율 (28번 응급실은 별도)
  최신성   병상: 수집시각 − 입력시각(hvidate) 시차. 15분·60분·8시간·24시간 초과 비율
           중증질환: API에 갱신시각이 없어 '값이 바뀌지 않고 8시간 이상 유지된' 비율로 측정
  일관성   (a) 수용가능 Y인데 같은 분류 차단 메시지가 동시에 게시된 비율
           (b) 가용 병상 > 기준 병상 (음수 가용은 오류가 아니라 과밀로 별도 집계)
           (c) 장비 가용 여부: Y 가용, N 보유하나 가용 불가, N1 미보유(코드). 보유 장비 중 N 비율
  메시지   기관·분류코드·문구·게시시각이 같은 것을 1건으로 묶음.
           관찰 지속시간 = 처음 보인 시점 ~ 마지막으로 보인 시점 + 30분 (기간 경계에 걸리면 절단 표시)
           예정 지속시간 = 게시시각 ~ 입력된 종료 예정시각
"""
from __future__ import annotations

import argparse
import glob
import re
from pathlib import Path

import pandas as pd

import nemc_api as api

HERE = Path(__file__).resolve().parent
SNAP = HERE / "snapshots"
OUT = HERE / "analysis_out"
SLOT = pd.Timedelta(minutes=30)

SEVERE_COLS = [f"MKioskTy{i}" for i in range(1, 28)]
MISSING = {"정보미제공", "", "nan", "None"}
YN_COLS_HINT = "ayn"   # hvctayn, hvmriayn … 가용 여부 항목

REASON_RULES = [   # 순서가 우선순위. 앞에서 걸리면 뒤는 보지 않음
    ("사유 미기재", r"^\s*[.\-]*\s*$|불가능메[세시]지등록"),
    ("수술·시술 중(이식 등)", r"수술 ?중|시술 ?중|(?<![A-Za-z])LT(?![A-Za-z])|이식|수술로 인한"),
    ("병상 부족", r"병상|중환자실|ICU|NICU|입원실|병실|만실|포화|과밀|산모실"),
    ("의료진 부재·부족", r"의료진|전문의|당직|인력|교수|휴진|학회|출장|마취|부재"),
    ("장비 문제", r"장비|고장|점검|(?<![A-Za-z])CT(?![A-Za-z])|MRI|혈관조영|앤지오|ECMO|투석|CRRT|인공호흡"),
    ("부분 제공·시간 제한", r"부분제공|부분 제공|정규시간|이후|평일|주간|야간|주말|공휴일|요일|오전|오후|\d{1,2}:\d{2}|시까지"),
    ("환자군 제한(신환·경증·연령 등)", r"신환|f/u|재진|기존 ?환자|등록 ?환자|타원|경증|연령|개월|세 이상|세 미만|체중|kg|500g|산모|임신"),
    ("진료과·시술 불가(사유 미상)", r"불가|불능|제한"),
    ("사전 확인 요청", r"사전 ?확인|사전 ?협의|확인 ?부탁|수용 ?여부|문의|연락"),
]


def load(kind: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(SNAP / f"{kind}_*.csv")))
    if not files:
        raise SystemExit(f"{kind}_*.csv 가 없습니다 ({SNAP})")
    df = pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False, encoding="utf-8-sig") for f in files],
                   ignore_index=True)
    df["collected_at"] = pd.to_datetime(df["collected_at"])
    df["slot"] = (df["collected_at"] + SLOT / 2).dt.floor("30min")
    return df


def ts14(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.str.strip(), format="%Y%m%d%H%M%S", errors="coerce")


def pct(n, d) -> str:
    return f"{100 * n / d:.1f}%" if d else "-"


def norm_sev(v: str) -> str:
    v = (v or "").strip()
    if v in MISSING:
        return "missing"
    if v.upper() == "Y":
        return "Y"
    if v.upper() == "N" or v == "불가능":
        return "N"
    return "other"


def classify_reason(text: str) -> str:
    for label, pat in REASON_RULES:
        if re.search(pat, text or "", flags=re.I):
            return label
    return "미분류"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sido", default="서울특별시")
    ap.add_argument("--start", default="2026-09-22T00:00")
    ap.add_argument("--end", default="2026-10-05T23:59")
    ap.add_argument("--national", action="store_true", help="전국 시도별 비교 표 추가")
    ap.add_argument("--include-reported", action="store_true", help="응급실운영신고기관(법정 응급의료기관 아님)도 포함")
    a = ap.parse_args()
    t0, t1 = pd.Timestamp(a.start), pd.Timestamp(a.end)
    OUT.mkdir(exist_ok=True)
    lines: list[str] = [f"# 데이터 품질 분석 요약 ({a.sido}, {t0:%Y-%m-%d %H:%M} ~ {t1:%Y-%m-%d %H:%M})", ""]

    beds_all, sev_all, msg_all = load("beds"), load("severe"), load("messages")
    master = pd.read_csv(HERE / "hospitals_master.csv", dtype=str, keep_default_na=False, encoding="utf-8-sig")
    emcls = dict(zip(master["hpid"], master["emcls_name"]))

    def cut(df):
        return df[(df["slot"] >= t0) & (df["slot"] <= t1)]

    beds = cut(beds_all[beds_all["sido"] == a.sido]).drop_duplicates(["slot", "hpid"])
    sev = cut(sev_all[sev_all["sido"] == a.sido]).drop_duplicates(["slot", "hpid"])
    msg = cut(msg_all[msg_all["sido"] == a.sido])
    for df in (beds, sev):
        df["type"] = df["hpid"].map(emcls).replace("", "미상").fillna("미상")
    excluded = []
    if not a.include_reported:
        excluded = sorted(sev.loc[sev.type == "응급실운영신고기관", "dutyName"].unique())
        beds = beds[beds.type != "응급실운영신고기관"]; sev = sev[sev.type != "응급실운영신고기관"]
        rep = {h for h, t in emcls.items() if t == "응급실운영신고기관"}
        msg = msg[~msg.hpid.isin(rep)]

    slots = sorted(set(beds["slot"]) | set(sev["slot"]))
    hp_b, hp_s = set(beds["hpid"]), set(sev["hpid"])
    hp_all = hp_b | hp_s
    type_n = pd.Series({h: emcls.get(h, "미상") or "미상" for h in hp_all}).value_counts()

    lines += ["## 1. 분석 대상",
              f"- 스냅샷 회차: {len(slots)}회 ({slots[0]:%m-%d %H:%M} ~ {slots[-1]:%m-%d %H:%M})",
              f"- 기관 수: {len(hp_all)}곳 — " + ", ".join(f"{k} {v}" for k, v in type_n.items()),
              f"- 제외: 응급실운영신고기관 {len(excluded)}곳 ({', '.join(excluded)}) — 법정 응급의료기관 아님, --include-reported 로 포함 가능",
              f"- 병상 목록 기관 {len(hp_b)} / 수용가능 목록 기관 {len(hp_s)}",
              f"- 수용가능 목록에만 있고 병상 정보가 전 기간 없는 기관: {len(hp_s - hp_b)}곳 "
              f"({', '.join(sorted(sev[sev.hpid.isin(hp_s - hp_b)].dutyName.unique()))})",
              f"- 병상 목록에만 있는 기관: {len(hp_b - hp_s)}곳", ""]

    # ---- 2. 완전성 ---------------------------------------------------------
    long = sev.melt(id_vars=["slot", "hpid", "type"], value_vars=[c for c in SEVERE_COLS if c in sev],
                    var_name="col", value_name="raw")
    long["n"] = long["col"].str.replace("MKioskTy", "").astype(int)
    long["v"] = long["raw"].map(norm_sev)
    long["missing"] = long["v"] == "missing"
    by_cat = (long.groupby("n")["missing"].mean().mul(100).round(1)
              .rename("미입력률(%)").to_frame())
    by_cat["분류"] = by_cat.index.map(api.SEVERE_TYPES)
    by_cat = by_cat[["분류", "미입력률(%)"]].sort_values("미입력률(%)", ascending=False)
    by_cat.to_csv(OUT / "completeness_by_category.csv", encoding="utf-8-sig")
    cen = long[long.type.str.contains("센터")]
    cen.groupby("n")["missing"].mean().mul(100).round(1).rename("센터급 미입력률(%)").to_frame().assign(
        분류=lambda d: d.index.map(api.SEVERE_TYPES)).to_csv(OUT / "completeness_by_category_centers.csv", encoding="utf-8-sig")
    by_type = long.groupby("type")["missing"].mean().mul(100).round(1).rename("미입력률(%)")
    by_type.to_csv(OUT / "completeness_by_type.csv", encoding="utf-8-sig")
    top = by_cat.head(3)
    lines += ["## 2. 완전성 (중증질환 27개 분류)",
              f"- 전체 미입력률: {pct(long['missing'].sum(), len(long))}",
              f"- 분류별 범위: {by_cat['미입력률(%)'].min():.1f}~{by_cat['미입력률(%)'].max():.1f}%",
              "- 미입력 상위: " + "; ".join(f"{r['분류']} {r['미입력률(%)']:.1f}%" for _, r in top.iterrows()),
              f"- 센터급(권역·지역센터) 미입력률: {pct(long[long.type.str.contains('센터')]['missing'].sum(), long.type.str.contains('센터').sum())} "
              "← 주 지표 (지역기관은 27개 분류 제공 의무·평가 대상 아님)",
              "- 종별: " + "; ".join(f"{k} {v:.1f}%" for k, v in by_type.items()),
              f"- 규격 밖 값(other) 비율: {pct((long['v'] == 'other').sum(), len(long))}", ""]

    # ---- 3. 최신성 ---------------------------------------------------------
    beds["hvidate_t"] = ts14(beds["hvidate"])
    beds["lag_min"] = (beds["collected_at"] - beds["hvidate_t"]).dt.total_seconds() / 60
    lag = beds["lag_min"].dropna()
    q = lag.quantile([.25, .5, .75])
    inst_med = beds.groupby("hpid")["lag_min"].median()
    auto = set(inst_med[inst_med <= 15].index)
    beds["crit"] = beds["hpid"].map(lambda h: 15 if h in auto else 60)
    over_crit = (beds["lag_min"] > beds["crit"]).sum()
    lag_type = beds.groupby("type")["lag_min"].median().round(1)
    beds[["slot", "hpid", "dutyName", "type", "hvidate", "lag_min"]].to_csv(OUT / "bed_lag.csv", index=False, encoding="utf-8-sig")

    # 중증질환 값 불변 지속
    long = long.sort_values(["hpid", "n", "slot"])
    long["chg"] = long.groupby(["hpid", "n"])["raw"].transform(lambda s: s.ne(s.shift()))
    long["run"] = long.groupby(["hpid", "n"])["chg"].cumsum()
    long["since"] = long["slot"] - long.groupby(["hpid", "n", "run"])["slot"].transform("min")
    eligible = long[long["slot"] >= slots[0] + pd.Timedelta(hours=8)]
    eligible = eligible[~eligible["missing"]]
    stale8 = (eligible["since"] >= pd.Timedelta(hours=8)).mean() if len(eligible) else float("nan")
    never_chg = long.groupby("hpid")["chg"].sum().eq(long.groupby("hpid")["n"].nunique())  # 첫 관측 외 변화 없음
    span_days = max((slots[-1] - slots[0]).total_seconds() / 86400, 1 / 48)
    n_changes = long.groupby("hpid")["chg"].sum() - long.groupby("hpid")["n"].nunique()   # 첫 관측 제외
    chg_per_day = n_changes / span_days
    chg_type = chg_per_day.groupby(chg_per_day.index.map(lambda h: emcls.get(h, "미상") or "미상")).median()
    chg_per_day.rename("값변경/일").to_csv(OUT / "severe_changes_per_day.csv", encoding="utf-8-sig")

    lines += ["## 3. 최신성",
              f"- 병상 입력시각 시차: 중앙값 {q[.5]:.1f}분 (IQR {q[.25]:.1f}~{q[.75]:.1f}분), 관측 {len(lag):,}건",
              f"- 시차 > 15분 {pct((lag > 15).sum(), len(lag))}, > 60분 {pct((lag > 60).sum(), len(lag))}, "
              f"> 8시간 {pct((lag > 480).sum(), len(lag))}, > 24시간 {pct((lag > 1440).sum(), len(lag))}",
              f"- 자동전송 추정 기관(시차 중앙값 ≤15분) {len(auto)}/{beds.hpid.nunique()}곳; "
              f"추정 방식별 기준(자동 15분·수동 60분) 초과 {pct(over_crit, len(beds))}",
              "- 종별 시차 중앙값(분): " + "; ".join(f"{k} {v}" for k, v in lag_type.items()),
              f"- 중증질환 값이 바뀌지 않고 8시간 이상 유지된 기관-분류-시점: {stale8 * 100:.1f}% (미입력 제외, 첫 8시간 제외)",
              f"- 분석 기간 동안 27개 분류 값이 한 번도 바뀌지 않은 기관: {int(never_chg.sum())}/{len(never_chg)}곳",
              f"- 값 변경 빈도: 기관당 하루 {chg_per_day.median():.2f}회(중앙값), 평균 {chg_per_day.mean():.2f}회; "
              "종별 중앙값 " + "; ".join(f"{k} {v:.2f}" for k, v in chg_type.items()), ""]

    # ---- 4. 일관성 ---------------------------------------------------------
    msg = msg.copy()
    msg["n"] = msg["symTypCod"].map(api.SYMTYP_TO_SEVERE)
    block = msg[(msg["symBlkMsgTyp"] == "중증") & (msg["symOutDspYon"] == "차단") & msg["n"].notna()]
    block_keys = set(zip(block["slot"], block["hpid"], block["n"].astype(int)))
    y = long[long["v"] == "Y"]
    y_conf = y[[k in block_keys for k in zip(y["slot"], y["hpid"], y["n"])]]

    over, neg_er, pairs = 0, 0, 0
    for av, base, _ in api.BED_FIELDS:
        if base and av in beds and base in beds:
            a_ = pd.to_numeric(beds[av], errors="coerce"); b_ = pd.to_numeric(beds[base], errors="coerce")
            ok = a_.notna() & b_.notna()
            pairs += ok.sum(); over += (ok & (a_ > b_)).sum()
    er = pd.to_numeric(beds.get("hvec"), errors="coerce")
    neg_er = (er < 0).sum()
    yn_cols = [c for c in beds.columns if c.endswith(YN_COLS_HINT)]
    yn_vals = beds[yn_cols].stack().str.strip()
    yn_bad = yn_vals[~yn_vals.isin(["Y", "N", "N1", ""])]          # N1 = 미보유(코드), 규격 안
    owned = yn_vals[yn_vals.isin(["Y", "N"])]                       # 보유 장비만
    eq_na = beds[yn_cols].apply(lambda s: s.str.strip()).apply(
        lambda s: round(100 * (s == "N").sum() / max(s.isin(["Y", "N"]).sum(), 1), 1))
    eq_na.rename("보유기관중 가용불가(%)").to_csv(OUT / "equipment_unavailable.csv", encoding="utf-8-sig")

    lines += ["## 4. 일관성",
              f"- 수용가능 Y인데 같은 분류 차단 메시지 동시 게시: {len(y_conf):,}/{len(y):,} ({pct(len(y_conf), len(y))})",
              f"- 가용 병상 > 기준 병상: {over:,}/{pairs:,} 항목-시점 ({pct(over, pairs)})",
              f"- 응급실 가용 음수(과밀, 오류 아님): {neg_er:,}/{er.notna().sum():,} 기관-시점 ({pct(neg_er, er.notna().sum())})",
              f"- 장비 가용 여부: 보유 기관-시점 중 가용 불가(N) {pct((owned == 'N').sum(), len(owned))} "
              f"(N1=미보유는 제외; 항목별은 equipment_unavailable.csv)",
              f"- 가용 여부 항목 규격 밖 값(Y·N·N1·빈칸 외): {len(yn_bad):,}/{len(yn_vals):,}", ""]

    # ---- 5. 수용불가 메시지 -------------------------------------------------
    key = ["hpid", "symTypCod", "symBlkMsg", "symBlkSttDtm"]
    g = msg.groupby(key).agg(dutyName=("dutyName", "first"), kind=("symBlkMsgTyp", "first"),
                             cat=("symTypCodMag", "first"), disp=("symOutDspYon", "first"),
                             dept=("trtPrtCodMag", "first"), end_plan=("symBlkEndDtm", "first"),
                             first=("slot", "min"), last=("slot", "max")).reset_index()
    g["obs_h"] = (g["last"] - g["first"] + SLOT).dt.total_seconds() / 3600
    g["censored"] = (g["first"] <= slots[0]) | (g["last"] >= slots[-1])
    g["start_t"], g["end_t"] = ts14(g["symBlkSttDtm"]), ts14(g["end_plan"])
    g["plan_h"] = (g["end_t"] - g["start_t"]).dt.total_seconds() / 3600
    g["end_midnight"] = g["end_t"].dt.strftime("%H%M%S").eq("000000")
    g["reason"] = g["symBlkMsg"].map(classify_reason)
    g.to_csv(OUT / "messages_unique.csv", index=False, encoding="utf-8-sig")
    g[g.reason == "미분류"][["dutyName", "kind", "cat", "symBlkMsg"]].to_csv(
        OUT / "messages_unclassified.csv", index=False, encoding="utf-8-sig")
    sev_msg, er_msg = g[g.kind == "중증"], g[g.kind != "중증"]
    unc = sev_msg[~sev_msg.censored]
    reasons = sev_msg["reason"].value_counts()
    cats = sev_msg["cat"].value_counts().head(5)

    lines += ["## 5. 수용불가 메시지 (중복 제거 후)",
              f"- 고유 메시지 {len(g):,}건: 중증질환 {len(sev_msg):,}건, 응급실 안내·제한 {len(er_msg):,}건",
              f"- 중증질환 메시지 관찰 지속시간 중앙값 {unc['obs_h'].median():.1f}시간 "
              f"(기간 경계에 걸리지 않은 {len(unc)}건 기준; 전체 {sev_msg['obs_h'].median():.1f}시간)",
              f"- 예정 지속시간 중앙값 {sev_msg['plan_h'].median():.1f}시간; 예정 종료가 00:00인 메시지 {pct(sev_msg['end_midnight'].sum(), len(sev_msg))}",
              "- 사유 유형: " + "; ".join(f"{k} {v}건({pct(v, len(sev_msg))})" for k, v in reasons.items()),
              "- 많은 분류: " + "; ".join(f"{k} {v}건" for k, v in cats.items()),
              f"- 미분류 문구는 analysis_out/messages_unclassified.csv 에서 직접 확인", ""]

    # ---- 6. 전국 비교 ------------------------------------------------------
    if a.national:
        rows = []
        bn = cut(beds_all).drop_duplicates(["slot", "hpid"]).copy()
        sn = cut(sev_all).drop_duplicates(["slot", "hpid"])
        mn = cut(msg_all)
        bn["lag"] = (bn["collected_at"] - ts14(bn["hvidate"])).dt.total_seconds() / 60
        days = (bn["slot"].max() - bn["slot"].min()).total_seconds() / 86400 or 1
        for sd, sub in sn.groupby("sido"):
            vals = sub[[c for c in SEVERE_COLS if c in sub]].stack().map(norm_sev)
            b = bn[bn.sido == sd]
            m = mn[(mn.sido == sd) & (mn.symBlkMsgTyp == "중증")].drop_duplicates(key)
            n_inst = sub.hpid.nunique()
            rows.append({"시도": sd, "기관수": n_inst,
                         "미입력률(%)": round(100 * (vals == "missing").mean(), 1),
                         "병상시차중앙값(분)": round(b["lag"].median(), 1),
                         "시차60분초과(%)": round(100 * (b["lag"] > 60).mean(), 1),
                         "중증메시지/기관/일": round(len(m) / n_inst / days, 2)})
        nat = pd.DataFrame(rows).sort_values("미입력률(%)", ascending=False)
        nat.to_csv(OUT / "national_by_sido.csv", index=False, encoding="utf-8-sig")
        lines += ["## 6. 전국 시도 비교", "",
                  "| " + " | ".join(nat.columns) + " |", "|" + "---|" * len(nat.columns)]
        lines += ["| " + " | ".join(str(v) for v in r) + " |" for r in nat.itertuples(index=False)]
        lines += [""]

    (OUT / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    main()
