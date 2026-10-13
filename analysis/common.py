"""공통 유틸: 스냅샷 로드, 기관 종별 결합, 비율 신뢰구간, 명령행 인자.

리포지토리 루트(= nemc_api.py, hospitals_master.csv, snapshots/ 가 있는 곳)를 기준으로 경로를 잡는다.
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import nemc_api as api  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
CL = ["권역응급의료센터", "지역응급의료센터", "지역응급의료기관"]
SH = {"권역응급의료센터": "권역", "지역응급의료센터": "지역센터", "지역응급의료기관": "지역기관"}
SEVERE_COLS = [f"MKioskTy{i}" for i in range(1, 28)]


def parse_args(desc: str, extra=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--snapshots", default=str(ROOT / "snapshots"), help="스냅샷 CSV 폴더")
    ap.add_argument("--start", default="2026-09-24 16:45", help="분석 시작(수집시각, KST)")
    ap.add_argument("--end", default="2026-10-13 00:00", help="분석 종료(미포함)")
    ap.add_argument("--sido", default="all", help="시도명 또는 all")
    ap.add_argument("--out", default=str(OUT), help="결과 폴더")
    ap.add_argument("--tag", default="", help="결과 파일명에 붙일 꼬리표")
    if extra:
        extra(ap)
    a = ap.parse_args()
    Path(a.out).mkdir(parents=True, exist_ok=True)
    return a


def load(kind: str, snapshots: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(Path(snapshots) / f"{kind}_*.csv")))
    if not files:
        sys.exit(f"{snapshots} 에 {kind}_YYYYMM.csv 가 없습니다.")
    return pd.concat(
        [pd.read_csv(f, dtype=str, encoding="utf-8-sig", keep_default_na=False) for f in files],
        ignore_index=True,
    )


def master() -> pd.DataFrame:
    return pd.read_csv(ROOT / "hospitals_master.csv", dtype=str, encoding="utf-8-sig")[["hpid", "emcls_name"]]


def prep(kind: str, a: argparse.Namespace) -> pd.DataFrame:
    """기간·시도 필터, 종별 결합, 30분 회차(rt) 부여."""
    d = load(kind, a.snapshots)
    if a.sido != "all":
        d = d[d.sido == a.sido]
    d = d.copy()
    d["t"] = pd.to_datetime(d.collected_at)
    d = d[(d.t >= pd.Timestamp(a.start)) & (d.t < pd.Timestamp(a.end))]
    d = d.merge(master(), on="hpid", how="left")
    d["emcls_name"] = d.emcls_name.fillna("미상")
    d["rt"] = d.t.dt.round("30min")
    return d


def wilson(k: float, n: float, z: float = 1.96):
    """비율(%)과 Wilson 95% 신뢰구간(%)."""
    if n == 0:
        return (np.nan, np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(100 * p, 1), round(100 * (c - h), 1), round(100 * (c + h), 1))


def outpath(a: argparse.Namespace, name: str) -> Path:
    stem, dot, ext = name.rpartition(".")
    return Path(a.out) / (f"{stem}_{a.tag}.{ext}" if a.tag else name)
