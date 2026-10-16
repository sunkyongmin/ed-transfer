"""맥(파일럿 수집기)과 서버(본 수집기) 병행 수집 자료 비교.

사용:
    python analysis/compare_collectors.py
    python analysis/compare_collectors.py --mac snapshots --server snapshots_server --start "2026-10-14 01:00"

회차 배정: 수집시각을 가장 가까운 30분 정시로 반올림, 정시와 5분 넘게 차이 나면 제외.
같은 회차·같은 기관이 여러 번 있으면 정시에 가장 가까운 1건만 남김.
비교 구간: --start 부터 두 자료에 공통으로 있는 마지막 회차까지.
"""
from __future__ import annotations

import argparse
import glob
from pathlib import Path

import pandas as pd

TOL = pd.Timedelta(minutes=5)
MSG_KEY_FULL = ["hpid", "symTypCod", "symBlkMsg", "symBlkSttDtm"]
MSG_KEY_NOTEXT = ["hpid", "symTypCod", "symBlkSttDtm"]


def load(folder: Path, kind: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(folder / f"{kind}_*.csv")))
    if not files:
        return pd.DataFrame()
    df = pd.concat((pd.read_csv(f, dtype=str, encoding="utf-8-sig") for f in files), ignore_index=True)
    df["ts"] = pd.to_datetime(df["collected_at"], errors="coerce")
    df = df.dropna(subset=["ts"])
    df["slot"] = df["ts"].dt.round("30min")
    df["off"] = (df["ts"] - df["slot"]).abs()
    df = df[df["off"] <= TOL]
    return df


def dedup_rows(df: pd.DataFrame) -> pd.DataFrame:
    """회차·기관별 정시에 가장 가까운 수집 시각(collected_at) 하나만 남김."""
    if df.empty:
        return df
    best = df.sort_values("off").groupby(["slot", "hpid"], as_index=False)["collected_at"].first()
    return df.merge(best, on=["slot", "hpid", "collected_at"])


def window(a: pd.DataFrame, b: pd.DataFrame, start: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    end = min(a["slot"].max(), b["slot"].max())
    f = lambda d: d[(d["slot"] >= start) & (d["slot"] <= end)]
    return f(a), f(b), end


def compare_panel(name: str, mac: pd.DataFrame, srv: pd.DataFrame, value_cols: list[str] | None = None) -> dict:
    mac, srv = dedup_rows(mac), dedup_rows(srv)
    slots = sorted(set(mac["slot"]) | set(srv["slot"]))
    cnt = pd.DataFrame({
        "mac": mac.groupby("slot")["hpid"].nunique(),
        "server": srv.groupby("slot")["hpid"].nunique(),
    }).reindex(slots).fillna(0).astype(int)
    cnt["diff"] = cnt["server"] - cnt["mac"]

    keys_m = set(zip(mac["slot"], mac["hpid"]))
    keys_s = set(zip(srv["slot"], srv["hpid"]))
    both = keys_m & keys_s

    dup_m = int(mac.duplicated(["slot", "hpid"]).sum())
    dup_s = int(srv.duplicated(["slot", "hpid"]).sum())
    cols = value_cols or sorted((set(mac.columns) & set(srv.columns)) - {"collected_at", "ts", "slot", "off", "sido", "dutyName", "hpid"})
    m = mac.drop_duplicates(["slot", "hpid"]).set_index(["slot", "hpid"])[cols].fillna("")
    s = srv.drop_duplicates(["slot", "hpid"]).set_index(["slot", "hpid"])[cols].fillna("")
    idx = m.index.intersection(s.index).sort_values()
    mv, sv = m.loc[idx].to_numpy(), s.loc[idx].to_numpy()
    same_row = pd.Series((mv == sv).all(axis=1))
    col_mismatch = pd.Series((mv != sv).sum(axis=0), index=cols).sort_values(ascending=False)
    col_mismatch = col_mismatch[col_mismatch > 0]

    return {
        "name": name,
        "slots": len(slots),
        "slot_counts": cnt,
        "only_mac": len(keys_m - keys_s),
        "only_server": len(keys_s - keys_m),
        "both": len(both),
        "dup_mac": dup_m,
        "dup_server": dup_s,
        "identical_pct": 100 * same_row.mean() if len(same_row) else float("nan"),
        "col_mismatch": col_mismatch.head(10),
    }


def compare_messages(mac: pd.DataFrame, srv: pd.DataFrame) -> dict:
    out = {}
    for label, key in (("본문 포함", MSG_KEY_FULL), ("본문 제외", MSG_KEY_NOTEXT)):
        km = set(map(tuple, mac[["slot"] + key].fillna("").drop_duplicates().values))
        ks = set(map(tuple, srv[["slot"] + key].fillna("").drop_duplicates().values))
        inter = len(km & ks)
        union = len(km | ks)
        out[label] = {"mac": len(km), "server": len(ks), "both": inter,
                      "only_mac": len(km - ks), "only_server": len(ks - km),
                      "jaccard_pct": 100 * inter / union if union else float("nan")}
    per_slot = pd.DataFrame({
        "mac": mac.drop_duplicates(["slot"] + MSG_KEY_NOTEXT).groupby("slot").size(),
        "server": srv.drop_duplicates(["slot"] + MSG_KEY_NOTEXT).groupby("slot").size(),
    }).fillna(0).astype(int)
    per_slot["diff"] = per_slot["server"] - per_slot["mac"]
    out["per_slot"] = per_slot
    return out


def coverage(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    planned = pd.date_range(start, end, freq="30min")
    got = df.groupby("sido")["slot"].nunique()
    return (got / len(planned) * 100).round(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mac", default="snapshots")
    ap.add_argument("--server", default="snapshots_server")
    ap.add_argument("--start", default="2026-10-14 01:00")
    a = ap.parse_args()
    start = pd.Timestamp(a.start)

    print(f"비교 시작 회차: {start}")
    verdict_ok = True
    for kind, vcols in (("beds", None), ("severe", None)):
        mac, srv = load(Path(a.mac), kind), load(Path(a.server), kind)
        if mac.empty or srv.empty:
            print(f"\n[{kind}] 파일 없음 (mac {len(mac)}행, server {len(srv)}행)"); verdict_ok = False; continue
        mac, srv, end = window(mac, srv, start)
        r = compare_panel(kind, mac, srv, vcols)
        c = r["slot_counts"]
        print(f"\n===== {kind} ({start} ~ {end}, {r['slots']}회차) =====")
        print(f"기관-회차: 양쪽 {r['both']:,} / 맥만 {r['only_mac']:,} / 서버만 {r['only_server']:,}")
        print(f"같은 회차 안에서 한 기관이 두 번 이상 나온 행(API 응답 중복): 맥 {r['dup_mac']:,} / 서버 {r['dup_server']:,}")
        print(f"회차별 기관 수가 다른 회차: {(c['diff'] != 0).sum()}개")
        if (c["diff"] != 0).any():
            print(c[c["diff"] != 0].to_string())
        print(f"양쪽 다 있는 기관-회차 중 모든 값이 같은 비율: {r['identical_pct']:.2f}%")
        if len(r["col_mismatch"]):
            print("값이 다른 칸이 많은 열(상위 10):")
            print(r["col_mismatch"].to_string())
        print("시도별 수집 충족률(%) 맥 / 서버:")
        cov = pd.DataFrame({"mac": coverage(mac, start, end), "server": coverage(srv, start, end)})
        print(cov.to_string())
        if r["only_mac"] or r["only_server"] or (c["diff"] != 0).any():
            verdict_ok = False

    mac, srv = load(Path(a.mac), "messages"), load(Path(a.server), "messages")
    if mac.empty or srv.empty:
        print("\n[messages] 파일 없음"); verdict_ok = False
    else:
        mac, srv, end = window(mac, srv, start)
        r = compare_messages(mac, srv)
        print(f"\n===== messages ({start} ~ {end}) =====")
        for label in ("본문 포함", "본문 제외"):
            x = r[label]
            print(f"[{label}] 맥 {x['mac']:,} / 서버 {x['server']:,} / 공통 {x['both']:,} / 맥만 {x['only_mac']:,} / 서버만 {x['only_server']:,} / 일치도 {x['jaccard_pct']:.2f}%")
        ps = r["per_slot"]
        print(f"회차별 메시지 건수가 다른 회차: {(ps['diff'] != 0).sum()}개 / {len(ps)}개")
        if (ps["diff"] != 0).any():
            print(ps[ps["diff"] != 0].head(20).to_string())
        if r["본문 제외"]["only_mac"] or r["본문 제외"]["only_server"]:
            verdict_ok = False

    print("\n판정:", "완전 일치 — 맥 수집기 종료 가능" if verdict_ok else "차이 있음 — 위 내용을 확인")


if __name__ == "__main__":
    main()
