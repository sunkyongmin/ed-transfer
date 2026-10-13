"""포스터 그림 4장 (300dpi PNG). quality.py 와 bootstrap_strata.py 의 결과(out/)를 읽는다.

실행 예:
    python analysis/figures.py --tag nat --n-label "44,139,233" --subtitle "전국 416개 응급의료기관, 2026-09-24~10-12, 30분 간격 876회차"
"""
from __future__ import annotations

import glob

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from common import CL, SH as TIER_KEY, outpath, parse_args  # noqa: E402

COLOR = {"권역응급의료센터": "#2a78d6", "지역응급의료센터": "#eb6834", "지역응급의료기관": "#1baf7a"}
TIERS = ["권역센터", "지역센터", "지역기관"]


def korean_font():
    cands = glob.glob("/System/Library/Fonts/AppleSDGothicNeo.ttc") + glob.glob("/Library/Fonts/NanumGothic*.ttf") \
        + glob.glob("/usr/share/fonts/**/NotoSansCJK*.ttc", recursive=True) + glob.glob("/usr/share/fonts/**/NanumGothic*.ttf", recursive=True)
    if cands:
        fm.fontManager.addfont(cands[0])
        return fm.FontProperties(fname=cands[0]).get_name()
    return "sans-serif"


def main():
    a = parse_args("포스터 그림", lambda ap: (ap.add_argument("--n-label", default="44,139,233"), ap.add_argument("--subtitle", default="전국 응급의료기관, 30분 간격")))
    ns = a.n_label.split(",")
    SH = {c: f"{t} (n={n})" for c, t, n in zip(CL, TIERS, ns)}
    plt.rcParams.update({"font.family": korean_font(), "font.size": 10, "axes.edgecolor": "#c3c2b7", "axes.labelcolor": "#52514e",
                         "xtick.color": "#52514e", "ytick.color": "#52514e", "axes.spines.top": False, "axes.spines.right": False,
                         "figure.facecolor": "white", "axes.facecolor": "#fcfcfb", "axes.unicode_minus": False})

    # 그림1 미입력 히트맵
    hm = pd.read_csv(outpath(a, "히트맵_미입력.csv"), index_col=0, encoding="utf-8-sig")
    hm = hm.loc[hm.mean(axis=1).sort_values().index]
    cmap = LinearSegmentedColormap.from_list("blue", ["#f4f8fd", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
    fig, ax = plt.subplots(figsize=(6.2, 9.2))
    im = ax.imshow(hm.values, cmap=cmap, vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(3)); ax.set_xticklabels([SH[c] for c in hm.columns])
    ax.set_yticks(range(len(hm))); ax.set_yticklabels(hm.index, fontsize=9)
    for i in range(len(hm)):
        for j in range(3):
            val = hm.values[i, j]
            ax.text(j, i, f"{val:.0f}", ha="center", va="center", fontsize=8.5, color="white" if val > 55 else "#0b0b0b")
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    ax.set_title(f"중증응급질환 분류별 수용가능 정보 미입력 비율 (%)\n{a.subtitle}", fontsize=10.5, loc="left", color="#0b0b0b")
    cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02); cb.outline.set_visible(False); cb.set_label("미입력 %", color="#52514e")
    fig.tight_layout(); fig.savefig(outpath(a, "그림1_미입력_히트맵.png"), dpi=300); plt.close()

    # 그림2 시차 누적분포
    b = pd.read_pickle(outpath(a, "beds_slim.pkl"))
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for c in CL:
        x = np.sort(b[b.emcls_name == c].lag.clip(upper=70).values)
        ax.step(x, np.arange(1, len(x) + 1) / len(x) * 100, where="post", color=COLOR[c], lw=2, label=SH[c])
    for th, lab in [(15, "자동전송 기준 15분"), (60, "수동전송 기준 60분")]:
        ax.axvline(th, color="#898781", ls="--", lw=1); ax.text(th + 1, 8, lab, color="#52514e", fontsize=9)
    ax.set_xlim(0, 70); ax.set_ylim(0, 101); ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.set_xlabel("기관 입력시각 → 수집시각 시차 (분, 70분에서 절단)"); ax.set_ylabel("기관-시점 누적 비율 (%)")
    ax.grid(axis="y", color="#e1e0d9", lw=0.6); ax.legend(frameon=False, loc="center right")
    med = round(b.lag.median(), 1); p60 = round(100 * (b.lag > 60).mean(), 1)
    ax.set_title(f"병상 정보 입력-수집 시차의 누적분포: 중앙값 {med}분, 60분 초과 {p60}%", fontsize=10.5, loc="left", color="#0b0b0b")
    fig.tight_layout(); fig.savefig(outpath(a, "그림2_시차_누적분포.png"), dpi=300); plt.close()

    # 그림3 시간대별 과밀 (bootstrap_strata.py 결과 필요)
    hp = outpath(a, "과밀_시간대.csv")
    if hp.exists():
        h = pd.read_csv(hp)
        fig, axes = plt.subplots(1, 2, figsize=(9, 3.9), sharey=True)
        for ax, hol, tt in zip(axes, [False, True], ["평일", "휴일(주말·공휴일)"]):
            d = h[h.holiday == hol]
            for c in CL:
                x = d[d.emcls_name == c].sort_values("h")
                ax.plot(x.h, x.over, color=COLOR[c], lw=2, label=SH[c])
                ax.text(23.3, x.over.iloc[-1], SH[c].split(" ")[0], color=COLOR[c], fontsize=9, va="center")
            ax.set_title(tt, fontsize=10.5, loc="left", color="#0b0b0b"); ax.set_xticks(range(0, 24, 4)); ax.set_xlabel("시각")
            ax.grid(axis="y", color="#e1e0d9", lw=0.6); ax.set_xlim(0, 26)
        axes[0].set_ylabel("응급실 과밀 시점 비율 (%)\n(가용병상 음수 = 재실 환자 > 기준병상)"); axes[0].legend(frameon=False, fontsize=9)
        fig.suptitle("시간대별 응급실 과밀", x=0.01, ha="left", fontsize=10.5, color="#0b0b0b")
        fig.tight_layout(); fig.savefig(outpath(a, "그림3_과밀_시간대.png"), dpi=300); plt.close()

    # 그림4 군집 부트스트랩 신뢰구간
    rp = outpath(a, "표_군집부트스트랩.csv")
    if rp.exists():
        R = pd.read_csv(rp, encoding="utf-8-sig"); R = R[R.종별 != "전체"]
        ind = ["수용가능 미입력", "응급실 과밀(가용<0)", "기준병상 절반 이상 여유"]
        col = dict(zip(TIERS, [COLOR[c] for c in CL]))
        fig, axes = plt.subplots(1, 3, figsize=(9.5, 3.2), sharey=True)
        for ax, nm in zip(axes, ind):
            d = R[R.지표 == nm].set_index("종별").loc[[TIER_KEY[c] for c in CL]]
            for i, t in enumerate(TIERS):
                r = d.iloc[i]
                ax.plot([r.군집부트스트랩_하한, r.군집부트스트랩_상한], [i, i], color=col[t], lw=2.5, solid_capstyle="round")
                ax.plot(r.추정치, i, "o", color=col[t], ms=8, mec="white", mew=1.5)
                lx, ha = (r.군집부트스트랩_상한 + 3, "left") if r.군집부트스트랩_상한 < 80 else (r.군집부트스트랩_하한 - 3, "right")
                ax.text(lx, i, f"{r.추정치:.1f}", ha=ha, va="center", fontsize=9, color="#0b0b0b")
            ax.set_yticks(range(3)); ax.set_yticklabels(TIERS); ax.set_xlim(0, 100); ax.set_ylim(2.6, -0.6)
            ax.set_title(nm, fontsize=10, loc="left", color="#0b0b0b"); ax.grid(axis="x", color="#e1e0d9", lw=0.6); ax.set_xlabel("기관-시점 비율 (%)")
        fig.suptitle("종별 추정치와 기관 군집 부트스트랩 95% 신뢰구간", x=0.01, ha="left", fontsize=10.5, color="#0b0b0b")
        fig.tight_layout(); fig.savefig(outpath(a, "그림4_종별_신뢰구간.png"), dpi=300); plt.close()
    print("완료:", a.out)


if __name__ == "__main__":
    main()
