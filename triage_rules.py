"""
규칙 기반 진단추정 엔진 (외부 전송 없음).

입력: 구조화 소견(Findings) + 자유 텍스트(키워드 추출)
출력: 감별진단 후보 목록 — 각 후보는 국립중앙의료원 중증질환 분류 번호(nemc_api.SEVERE_TYPES)와
      필요 자원(장비·병상 필드), 근거, 점수를 가짐.

※ 임상 판단 보조용 휴리스틱입니다. 진단·전원 결정은 의사가 합니다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Callable

# ---------------------------------------------------------------------------
# 소견 구조
# ---------------------------------------------------------------------------

@dataclass
class Findings:
    # 인구학
    age: float | None = None          # 세 (영유아는 소수점 가능: 0.5 = 6개월)
    sex: str = ""                     # "M"/"F"
    pregnant: bool = False
    gest_weeks: float | None = None

    # 주호소 (다중 선택)
    cc: set[str] = field(default_factory=set)
    onset_hours: float | None = None  # 증상 발생 후 경과 시간

    # 활력징후·의식
    sbp: float | None = None
    hr: float | None = None
    rr: float | None = None
    spo2: float | None = None
    bt: float | None = None
    gcs: int | None = None
    mental: str = ""                  # alert/verbal/pain/unresponsive

    # 신경학
    focal_deficit: bool = False       # 편마비·언어장애·시야결손 등
    thunderclap: bool = False
    seizure: bool = False

    # 심전도·심장
    ecg: str = ""                     # normal / ste / st_t / arrhythmia / lbbb
    troponin_pos: bool = False
    pulse_deficit: bool = False       # 좌우 맥박·혈압 차

    # 검사
    k: float | None = None
    cr: float | None = None
    glucose: float | None = None
    hb: float | None = None
    lactate: float | None = None
    wbc: float | None = None
    crp: float | None = None
    bilirubin: float | None = None
    lipase: float | None = None
    esrd: bool = False                # 투석 환자
    pulm_edema: bool = False

    # 영상 (다중 선택)
    imaging: set[str] = field(default_factory=set)

    # 외상·특수
    trauma: bool = False
    burn_tbsa: float | None = None
    burn_special: bool = False        # 안면·손·회음부·흡입화상·전기화상
    amputation: str = ""              # none / digit / major
    foreign_body_airway: bool = False
    eye_emergency: str = ""           # none / chemical / vision_loss / open_globe
    psych: str = ""                   # none / suicidal / violent / psychotic
    vaginal_bleeding: bool = False
    labor: bool = False
    hematemesis_melena: bool = False
    hematochezia: bool = False
    hemoptysis_massive: bool = False

    free_text: str = ""

    def to_dict(self):
        d = asdict(self)
        d["cc"] = sorted(self.cc); d["imaging"] = sorted(self.imaging)
        return d


CC_OPTIONS = ["흉통", "호흡곤란", "복통", "두통", "의식저하", "실신", "발열", "구토", "설사", "요통/등통증",
              "편마비/언어장애", "경련", "질출혈", "토혈/흑색변", "혈변", "객혈", "외상", "화상", "안구 증상",
              "정신과적 증상", "황달", "핍뇨/무뇨", "다리 부종/통증"]

IMAGING_OPTIONS = ["CT 뇌출혈", "CT 지주막하출혈", "CT 뇌경색/대혈관폐색", "CT 정상(뇌)", "CT 대동맥박리", "CT 대동맥류",
                   "CT free air/천공", "CT 장폐색", "CT 허혈성 장질환", "CT 충수염", "CT 담낭염", "CT/US 담관확장·담관결석",
                   "US 담낭염", "US 장중첩", "CXR 종격동 확장", "CXR 폐부종", "CXR 기흉", "US 자궁외임신 의심", "영상 없음"]

# 자유 텍스트 키워드 → 소견 플래그
_KEYWORDS: list[tuple[str, Callable[[Findings], None]]] = [
    (r"st\s*(분절)?\s*상승|st[- ]?elevation|stemi", lambda f: setattr(f, "ecg", "ste")),
    (r"트로포닌\s*(양성|상승|\+)|troponin\s*(\+|pos|상승|elevat)", lambda f: setattr(f, "troponin_pos", True)),
    (r"편마비|hemiparesis|hemiplegia|실어|aphasia|구음장애|dysarthria|안면마비|facial (palsy|droop)|시야", lambda f: setattr(f, "focal_deficit", True)),
    (r"벼락|thunderclap|worst headache|생애 최악", lambda f: setattr(f, "thunderclap", True)),
    (r"경련|seizure|convuls", lambda f: setattr(f, "seizure", True)),
    (r"찢어지|tearing|ripping|맥박\s*차|pulse deficit|혈압\s*차", lambda f: setattr(f, "pulse_deficit", True)),
    (r"토혈|hematemesis|흑색변|melena|black stool", lambda f: setattr(f, "hematemesis_melena", True)),
    (r"혈변|hematochezia|bloody stool|bloody diarrhea", lambda f: setattr(f, "hematochezia", True)),
    (r"대량\s*객혈|massive hemoptysis", lambda f: setattr(f, "hemoptysis_massive", True)),
    (r"투석|dialysis|esrd|ckd\s*5", lambda f: setattr(f, "esrd", True)),
    (r"폐부종|pulmonary edema", lambda f: setattr(f, "pulm_edema", True)),
    (r"이물|foreign body|aspiration|기도\s*폐쇄", lambda f: setattr(f, "foreign_body_airway", True)),
    (r"절단|amputat", lambda f: setattr(f, "amputation", f.amputation or "digit")),
    (r"자살|suicid|자해|self[- ]?harm", lambda f: setattr(f, "psych", "suicidal")),
    (r"난폭|폭력|violent|agitat", lambda f: setattr(f, "psych", f.psych or "violent")),
    (r"화학\s*(물질|손상)|chemical (burn|injury)|양잿물|alkali", lambda f: setattr(f, "eye_emergency", f.eye_emergency or "chemical") if "안구" in f.free_text or "eye" in f.free_text.lower() else None),
    (r"시력\s*(소실|저하)|vision loss|amaurosis", lambda f: setattr(f, "eye_emergency", "vision_loss")),
    (r"안구\s*파열|open globe|globe rupture", lambda f: setattr(f, "eye_emergency", "open_globe")),
    (r"임신|pregnan|gravid", lambda f: setattr(f, "pregnant", True)),
    (r"진통|labor|contraction|양막\s*파수|rom\b", lambda f: setattr(f, "labor", True)),
    (r"질출혈|vaginal bleeding|pv bleeding", lambda f: setattr(f, "vaginal_bleeding", True)),
    (r"지주막하|subarachnoid|sah\b", lambda f: f.imaging.add("CT 지주막하출혈")),
    (r"뇌출혈|뇌내출혈|ich\b|intracerebral|intracranial h", lambda f: f.imaging.add("CT 뇌출혈")),
    (r"대혈관\s*폐색|lvo\b|large vessel|m1\b|ica\s*occlusion|basilar", lambda f: f.imaging.add("CT 뇌경색/대혈관폐색")),
    (r"대동맥\s*박리|aortic dissection|dissection", lambda f: f.imaging.add("CT 대동맥박리")),
    (r"대동맥류|aneurysm|aaa\b", lambda f: f.imaging.add("CT 대동맥류")),
    (r"free air|천공|perforat|pneumoperitoneum", lambda f: f.imaging.add("CT free air/천공")),
    (r"장폐색|ileus|obstruction", lambda f: f.imaging.add("CT 장폐색")),
    (r"장간막|mesenteric|허혈성 장|bowel ischemia", lambda f: f.imaging.add("CT 허혈성 장질환")),
    (r"충수염|appendicitis", lambda f: f.imaging.add("CT 충수염")),
    (r"담낭염|cholecystitis", lambda f: f.imaging.add("CT 담낭염")),
    (r"담관염|cholangitis|담관\s*(결석|확장)|cbd stone|choledocholithiasis", lambda f: f.imaging.add("CT/US 담관확장·담관결석")),
    (r"장중첩|intussusception", lambda f: f.imaging.add("US 장중첩")),
    (r"자궁외|ectopic", lambda f: f.imaging.add("US 자궁외임신 의심")),
    (r"종격동\s*확장|widened mediastinum", lambda f: f.imaging.add("CXR 종격동 확장")),
    (r"기흉|pneumothorax", lambda f: f.imaging.add("CXR 기흉")),
]


def apply_free_text(f: Findings) -> list[str]:
    """자유 텍스트에서 키워드를 추출해 Findings를 갱신. 적용된 키워드 목록 반환."""
    hits = []
    t = f.free_text or ""
    for pat, fn in _KEYWORDS:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            fn(f)
            hits.append(m.group(0))
    return hits


# ---------------------------------------------------------------------------
# 규칙
# ---------------------------------------------------------------------------

@dataclass
class Dx:
    name: str
    category: int | None          # nemc_api.SEVERE_TYPES 번호 (None: 특정 분류 없음)
    score: int
    resources: list[str]          # nemc_api BED/EQUIP 필드 키
    urgency: str                  # "즉시" / "긴급" / "준긴급"
    rationale: list[str]
    note: str = ""


def _is(v, lo=None, hi=None):
    return v is not None and (lo is None or v >= lo) and (hi is None or v <= hi)


def _shock(f: Findings) -> bool:
    return _is(f.sbp, hi=90) or _is(f.lactate, lo=4) or (_is(f.hr, lo=120) and _is(f.sbp, hi=100))


def evaluate(f: Findings) -> list[Dx]:
    out: list[Dx] = []
    infant = _is(f.age, hi=6)      # 영유아 분류 기준(대략 6세 이하)
    child = _is(f.age, hi=15)
    cc = f.cc
    img = f.imaging

    def add(name, cat, score, res, urg, why, note=""):
        if score > 0:
            out.append(Dx(name, cat, min(score, 100), res, urg, why, note))

    # ---- 심혈관 ----
    s, why = 0, []
    if "흉통" in cc: s += 20; why.append("흉통")
    if f.ecg == "ste": s += 60; why.append("ST 상승")
    if f.ecg == "lbbb": s += 25; why.append("새로운 LBBB(추정)")
    if f.troponin_pos: s += 30; why.append("troponin 양성")
    if f.ecg == "st_t": s += 15; why.append("ST/T 변화")
    if s >= 40:
        add("급성 심근경색(STEMI/NSTEMI)", 1, s, ["hvangioayn", "hv34"], "즉시" if f.ecg == "ste" else "긴급", why,
            "재관류(PCI) 가능 기관. STEMI는 door-to-balloon 고려해 가장 가까운 PCI 기관 우선")

    s, why = 0, []
    if "CT 대동맥박리" in img: s += 80; why.append("CT 대동맥박리")
    if f.pulse_deficit: s += 30; why.append("찢어지는 통증/맥박차")
    if "CXR 종격동 확장" in img: s += 20; why.append("종격동 확장")
    if ("흉통" in cc or "요통/등통증" in cc) and s: s += 10; why.append("흉/배부 통증")
    if s >= 30:
        add("급성 대동맥 증후군(흉부)", 5, s, ["hvccc", "hvctayn", "hvoc"], "즉시", why, "흉부외과 응급수술 가능 기관")
    if "CT 대동맥류" in img:
        add("복부 대동맥류(파열 의심)", 6, 70 + (15 if _shock(f) else 0), ["hvoc", "hvicc", "hvctayn"], "즉시",
            ["CT 대동맥류"] + (["쇼크"] if _shock(f) else []), "혈관외과 수술/EVAR 가능 기관")

    # ---- 신경 ----
    s, why = 0, []
    if "CT 지주막하출혈" in img: s += 85; why.append("CT SAH")
    if f.thunderclap: s += 40; why.append("벼락두통")
    if s:
        add("지주막하출혈", 3, s, ["hv6", "hvangioayn", "hvctayn"], "즉시", why, "뇌혈관 수술/코일색전 가능 기관")
    if "CT 뇌출혈" in img:
        s = 85 + (10 if f.mental in ("pain", "unresponsive") or _is(f.gcs, hi=8) else 0)
        add("뇌실질내출혈", 4, s, ["hv6", "hvctayn"], "즉시", ["CT 뇌출혈"], "신경외과 수술 가능 기관")
    s, why = 0, []
    if f.focal_deficit: s += 45; why.append("국소 신경학적 결손")
    if "편마비/언어장애" in cc: s += 20; why.append("편마비/언어장애 주호소")
    if "CT 뇌경색/대혈관폐색" in img: s += 40; why.append("CT 대혈관폐색/뇌경색")
    if "CT 정상(뇌)" in img and s: s += 10; why.append("CT 출혈 없음")
    if "CT 뇌출혈" in img or "CT 지주막하출혈" in img: s = 0
    if s >= 40:
        note = "혈전용해(4.5h)·혈전제거술(24h) 가능 기관"
        if f.onset_hours is not None:
            note += f" — 발생 후 {f.onset_hours:.1f}h"
            if f.onset_hours <= 4.5: s += 15; why.append("발생 4.5h 이내")
            elif f.onset_hours <= 24: s += 5; why.append("발생 24h 이내")
        add("급성 허혈성 뇌졸중", 2, s, ["hvangioayn", "hvctayn", "hvcc"], "즉시", why, note)

    # ---- 소화기·복부 ----
    if "CT free air/천공" in img:
        add("소화관 천공", 9, 90, ["hvoc", "hvicc"], "즉시", ["CT free air"], "응급 개복/복강경 수술 가능 기관")
    if "CT 허혈성 장질환" in img:
        add("급성 장간막 허혈", 9, 85 + (10 if _is(f.lactate, lo=2) else 0), ["hvoc", "hvicc"], "즉시", ["CT 허혈성 장질환"])
    if "CT 장폐색" in img:
        add("장폐색", 9, 65 + (15 if _shock(f) or _is(f.lactate, lo=2) else 0), ["hvoc"], "긴급", ["CT 장폐색"], "교액 의심 시 응급수술")
    if "CT 충수염" in img:
        add("급성 충수염", 9, 60, ["hvoc"], "긴급", ["CT 충수염"])
    s, why = 0, []
    if "CT 담낭염" in img or "US 담낭염" in img: s += 65; why.append("영상 담낭염")
    if s and _is(f.bt, lo=38): s += 10; why.append("발열")
    if s and _is(f.wbc, lo=12): s += 5; why.append("백혈구 증가")
    if s:
        add("급성 담낭염", 7, s, ["hvoc"], "긴급", why, "담낭절제/PTGBD 가능 기관")
    s, why = 0, []
    if "CT/US 담관확장·담관결석" in img: s += 60; why.append("담관확장/결석")
    if _is(f.bilirubin, lo=2) or "황달" in cc: s += 15; why.append("황달/빌리루빈 상승")
    if s and _is(f.bt, lo=38): s += 15; why.append("발열(Charcot)")
    if s and _shock(f): s += 10; why.append("쇼크(Reynolds)")
    if s >= 40:
        add("급성 담관염/담관결석", 8, s, ["hvoc", "hvicc"], "즉시" if s >= 80 else "긴급", why, "ERCP 가능 기관")
    if "US 장중첩" in img or (infant and "복통" in cc and f.hematochezia):
        add("장중첩증(영유아)", 10, 80 if "US 장중첩" in img else 45, ["hvoc", "hv32"], "긴급",
            ["US 장중첩"] if "US 장중첩" in img else ["영유아 복통+혈변"], "소아외과/공기정복 가능 기관")

    # ---- 위장관 출혈·내시경 ----
    if f.hematemesis_melena or "토혈/흑색변" in cc:
        s = 55 + (20 if _is(f.hb, hi=8) else 0) + (15 if _shock(f) else 0)
        add("상부위장관 출혈", 12 if infant else 11, s, ["hvicc"], "즉시" if s >= 80 else "긴급",
            ["토혈/흑색변"] + (["Hb 저하"] if _is(f.hb, hi=8) else []) + (["쇼크"] if _shock(f) else []), "응급 내시경 가능 기관")
    if f.foreign_body_airway:
        add("기도 이물", 14 if infant else 13, 75 + (15 if _is(f.spo2, hi=90) else 0), ["hvventiayn"], "즉시",
            ["기도 이물"], "응급 기관지내시경 가능 기관")

    # ---- 산부인과 ----
    if f.pregnant or f.labor or f.vaginal_bleeding:
        if f.labor or (f.pregnant and _is(f.gest_weeks, lo=20) and "복통" in cc):
            s = 70 if f.labor else 40
            add("분만 진행/조기진통", 16, s, ["hv42"], "긴급", ["진통/양막파수" if f.labor else "임신 20주+ 복통"],
                "분만실 운영 기관" + (" (조산: 신생아중환자실 필요)" if _is(f.gest_weeks, hi=37) else ""))
        if "US 자궁외임신 의심" in img or (f.pregnant and _is(f.gest_weeks, hi=14) and (f.vaginal_bleeding or "복통" in cc)):
            s = 80 if "US 자궁외임신 의심" in img else 45
            add("자궁외임신(파열 의심)", 17, s + (15 if _shock(f) else 0), ["hvoc"], "즉시" if _shock(f) else "긴급",
                ["초기 임신 + 복통/출혈"] + (["쇼크"] if _shock(f) else []), "산과 응급수술 가능 기관")
        if f.pregnant and _is(f.gest_weeks, lo=20) and f.vaginal_bleeding:
            add("태반조기박리/전치태반 출혈", 17, 60 + (20 if _shock(f) else 0), ["hvoc", "hv42"], "즉시",
                ["임신 후반 질출혈"], "응급 제왕절개 가능 기관")
        if not f.pregnant and f.vaginal_bleeding and "복통" in cc and f.sex == "F":
            add("부인과 응급(난소낭종 파열/염전 등)", 18, 40 + (15 if _shock(f) else 0), ["hvoc"], "긴급", ["비임신 여성 복통+출혈"])
    if f.pregnant and _is(f.gest_weeks, hi=32) and (f.labor or "복통" in cc):
        add("조산·저체중출생아 집중치료 필요", 15, 50, ["hvncc", "hvincuayn"], "긴급", ["32주 미만 분만 임박"], "NICU 보유 기관")

    # ---- 화상·외상·접합 ----
    if f.burn_tbsa is not None or f.burn_special or "화상" in cc:
        s = 30
        if _is(f.burn_tbsa, lo=20): s += 50
        elif _is(f.burn_tbsa, lo=10): s += 30
        if f.burn_special: s += 35
        if child and _is(f.burn_tbsa, lo=10): s += 15
        add("중증 화상", 19, s, ["hv8", "hv43"], "즉시" if s >= 70 else "긴급",
            [f"TBSA {f.burn_tbsa}%" if f.burn_tbsa is not None else "화상"] + (["특수부위/흡입"] if f.burn_special else []),
            "화상 전문치료 기관")
    if f.amputation == "digit":
        add("수족지 절단", 20, 85, ["hvoc"], "즉시", ["수족지 절단"], "재접합 가능 기관, 허혈시간 6h 이내")
    elif f.amputation == "major":
        add("사지 절단(수족지 외)", 21, 90, ["hvoc", "hvicc"], "즉시", ["사지 절단"], "재접합/혈관수술 가능 기관")
    if f.trauma or "외상" in cc:
        s = 30 + (30 if _shock(f) else 0) + (20 if _is(f.gcs, hi=12) else 0)
        if s >= 50:
            add("중증 외상", None, s, ["hv9", "hv60", "hvoc"], "즉시", ["외상"] + (["쇼크"] if _shock(f) else []) + (["GCS≤12"] if _is(f.gcs, hi=12) else []),
                "권역외상센터 이송 고려 (본 API의 중증질환 분류 외 항목)")

    # ---- 신장·투석 ----
    s, why = 0, []
    if _is(f.k, lo=6.5): s += 60; why.append(f"K {f.k}")
    elif _is(f.k, lo=6.0): s += 40; why.append(f"K {f.k}")
    if f.pulm_edema or "CXR 폐부종" in img: s += 30; why.append("폐부종")
    if f.esrd: s += 25; why.append("투석 환자")
    if _is(f.cr, lo=5) and not f.esrd: s += 20; why.append(f"Cr {f.cr}")
    if s >= 40:
        cat = 23 if _shock(f) else 22
        add("응급 투석 필요(고칼륨혈증/체액과다/요독증)", cat, s, ["hvcrrtayn" if cat == 23 else "hvicc"], "즉시" if s >= 70 else "긴급", why,
            "불안정 시 CRRT, 안정 시 HD 가능 기관")

    # ---- 정신과·안과 ----
    if f.psych in ("suicidal", "violent", "psychotic") or "정신과적 증상" in cc:
        add("정신과적 응급(폐쇄병동 입원 필요)", 24, 70 if f.psych else 40, ["hv40"], "긴급",
            [{"suicidal": "자살 위험", "violent": "폭력/난폭", "psychotic": "급성 정신병적 상태"}.get(f.psych, "정신과적 증상")],
            "폐쇄병동 보유 기관")
    if f.eye_emergency in ("chemical", "vision_loss", "open_globe") or "안구 증상" in cc:
        add("안과적 응급", 25, 75 if f.eye_emergency else 40, [], "즉시" if f.eye_emergency == "chemical" else "긴급",
            [{"chemical": "화학 손상", "vision_loss": "급성 시력소실", "open_globe": "안구 파열"}.get(f.eye_emergency, "안구 증상")],
            "안과 응급 진료 가능 기관")

    # ---- 혈관중재 ----
    s, why = 0, []
    if f.hemoptysis_massive or "객혈" in cc: s += 50 if f.hemoptysis_massive else 25; why.append("객혈")
    if f.hematochezia and _shock(f): s += 45; why.append("하부위장관 출혈+쇼크")
    if s >= 45:
        add("응급 혈관색전술 필요 출혈", 27 if infant else 26, s, ["hvangioayn", "hvicc"], "즉시", why, "영상의학 혈관중재 가능 기관")

    # ---- 비특이적 중증 (분류 없음) ----
    s, why = 0, []
    if _shock(f): s += 40; why.append("쇼크/저혈압")
    if _is(f.spo2, hi=90) or _is(f.rr, lo=30): s += 30; why.append("호흡부전")
    if _is(f.gcs, hi=8) or f.mental == "unresponsive": s += 30; why.append("의식저하(GCS≤8)")
    if _is(f.bt, lo=38.3) and (_is(f.wbc, lo=12) or _is(f.crp, lo=10)) and s: s += 10; why.append("패혈증 의심")
    if s >= 40 and not any(d.urgency == "즉시" for d in out):
        add("비특이적 중증(패혈증/호흡부전/쇼크) — 중환자실 필요", 28, s, ["hvicc", "hvventiayn"], "즉시", why,
            "중환자실·인공호흡기 가용 기관 (응급실 수용가능 기준으로 조회)")

    # 정렬: 점수 내림차순
    out.sort(key=lambda d: -d.score)
    return out


def default_form_findings(**kw) -> Findings:
    f = Findings(**kw)
    apply_free_text(f)
    return f


# ---------------------------------------------------------------------------
# 진단명 직접 입력/선택용 카탈로그
# (표시명, 검색 별칭들, 중증질환 분류, 필요 자원, 비고)
# ---------------------------------------------------------------------------
DIAGNOSIS_CATALOG: list[tuple[str, list[str], int | None, list[str], str]] = [
    ("급성 심근경색 (STEMI)", ["stemi", "st elevation", "심근경색", "ami", "mi"], 1, ["hvangioayn", "hv34"], "일차 PCI 가능 기관, door-to-balloon 고려"),
    ("급성 심근경색 (NSTEMI) / 불안정협심증", ["nstemi", "acs", "불안정 협심증", "unstable angina", "급성관상동맥증후군"], 1, ["hvangioayn", "hv34"], "조기 침습 전략 가능 기관"),
    ("급성 허혈성 뇌졸중", ["뇌경색", "stroke", "ischemic stroke", "cerebral infarction", "뇌졸중", "대혈관폐색", "lvo"], 2, ["hvangioayn", "hvctayn", "hvcc"], "혈전용해(4.5h)·혈전제거술(24h) 가능 기관"),
    ("지주막하출혈", ["sah", "subarachnoid", "지주막하", "거미막하", "동맥류 파열"], 3, ["hv6", "hvangioayn", "hvctayn"], "뇌동맥류 클리핑/코일색전 가능 기관"),
    ("뇌실질내출혈 / 기타 뇌출혈", ["ich", "뇌출혈", "intracerebral", "뇌내출혈", "경막하", "sdh", "경막외", "edh", "소뇌출혈"], 4, ["hv6", "hvctayn"], "신경외과 응급수술 가능 기관"),
    ("급성 대동맥박리 (흉부)", ["대동맥박리", "aortic dissection", "dissection", "박리"], 5, ["hvccc", "hvctayn", "hvoc"], "흉부외과 응급수술 가능 기관"),
    ("복부 대동맥류 (파열/절박파열)", ["aaa", "대동맥류", "aneurysm", "복부대동맥류"], 6, ["hvoc", "hvicc", "hvctayn"], "혈관외과 수술/EVAR 가능 기관"),
    ("급성 담낭염", ["담낭염", "cholecystitis", "gb empyema", "담낭"], 7, ["hvoc"], "담낭절제/PTGBD 가능 기관"),
    ("급성 담관염 / 담관결석 / 담도 질환", ["담관염", "cholangitis", "cbd stone", "담관결석", "담도", "총담관"], 8, ["hvoc", "hvicc"], "ERCP 가능 기관"),
    ("소화관 천공", ["천공", "perforation", "free air", "복막염", "peritonitis"], 9, ["hvoc", "hvicc"], "응급 개복/복강경 수술 가능 기관"),
    ("급성 충수염", ["충수염", "appendicitis", "맹장"], 9, ["hvoc"], ""),
    ("장폐색 / 교액성 장폐색", ["장폐색", "ileus", "obstruction", "장꼬임", "volvulus", "탈장 감돈", "incarcerated hernia"], 9, ["hvoc"], "교액 의심 시 응급수술"),
    ("급성 장간막 허혈", ["장간막", "mesenteric ischemia", "허혈성 장", "smA occlusion"], 9, ["hvoc", "hvicc"], ""),
    ("급성 췌장염 (중증)", ["췌장염", "pancreatitis"], 9, ["hvicc"], "중증도에 따라 중환자실"),
    ("장중첩증 (영유아)", ["장중첩", "intussusception"], 10, ["hvoc", "hv32"], "소아외과/공기정복 가능 기관"),
    ("상부위장관 출혈 (성인)", ["ugib", "위장관 출혈", "토혈", "hematemesis", "흑색변", "melena", "정맥류 출혈", "variceal"], 11, ["hvicc"], "응급 내시경 가능 기관"),
    ("위장관 출혈 / 응급내시경 (영유아)", ["소아 위장관 출혈", "영유아 내시경", "소아 이물 삼킴", "battery ingestion", "건전지"], 12, ["hv32"], "소아 응급내시경 가능 기관"),
    ("기도 이물 (성인)", ["기도 이물", "airway foreign body", "aspiration", "기관지 이물"], 13, ["hvventiayn"], "응급 기관지내시경 가능 기관"),
    ("기도 이물 (영유아)", ["소아 기도 이물", "영유아 이물"], 14, ["hvventiayn", "hv32"], "소아 기관지내시경 가능 기관"),
    ("조산 / 저체중출생아 집중치료", ["조산", "preterm", "저체중", "lbw", "미숙아", "nicu"], 15, ["hvncc", "hvincuayn"], "NICU 보유 기관"),
    ("분만 진행 / 조기진통", ["분만", "labor", "진통", "delivery", "양막파수", "prom"], 16, ["hv42"], "분만실 운영 기관"),
    ("산과 응급수술 (자궁외임신·태반조기박리·전치태반 등)", ["자궁외임신", "ectopic", "태반조기박리", "abruption", "전치태반", "previa", "자궁파열", "산과 수술", "제왕절개"], 17, ["hvoc", "hv42"], "산과 응급수술 가능 기관"),
    ("부인과 응급수술 (난소낭종 파열·염전 등)", ["난소 염전", "ovarian torsion", "난소낭종 파열", "골반염", "pid", "부인과"], 18, ["hvoc"], ""),
    ("중증 화상", ["화상", "burn", "흡입화상", "전기화상", "화학화상"], 19, ["hv8", "hv43"], "화상 전문치료 기관"),
    ("수족지 절단", ["수지 절단", "손가락 절단", "finger amputation", "족지 절단", "발가락"], 20, ["hvoc"], "재접합 가능 기관, 허혈시간 6h 이내"),
    ("사지 절단 (수족지 외)", ["사지 절단", "amputation", "상완 절단", "하지 절단"], 21, ["hvoc", "hvicc"], "재접합/혈관수술 가능 기관"),
    ("응급 혈액투석 (고칼륨혈증·체액과다·요독증)", ["고칼륨", "hyperkalemia", "투석", "hd", "요독증", "uremia", "체액과다", "폐부종 투석"], 22, ["hvicc"], "응급 HD 가능 기관"),
    ("응급 CRRT (불안정 신부전·패혈성 쇼크)", ["crrt", "지속적 신대체", "불안정 신부전", "패혈성 쇼크 투석"], 23, ["hvcrrtayn", "hvicc"], "CRRT 가능 기관"),
    ("정신과적 응급 (폐쇄병동 입원)", ["정신과", "자살", "suicide", "자해", "급성 정신병", "psychosis", "폐쇄병동", "난폭"], 24, ["hv40"], "폐쇄병동 보유 기관"),
    ("안과적 응급", ["안과", "안구", "eye", "화학 안손상", "시력 소실", "안구 파열", "open globe", "망막", "급성 폐쇄각 녹내장"], 25, [], "안과 응급 진료 가능 기관"),
    ("응급 혈관색전술 (성인: 대량 객혈·위장관/외상 출혈 등)", ["색전술", "embolization", "혈관중재", "대량 객혈", "hemoptysis", "산후출혈", "pph", "골반 출혈"], 26, ["hvangioayn", "hvicc"], "영상의학 혈관중재 가능 기관"),
    ("응급 혈관색전술 (영유아)", ["소아 색전술", "영유아 혈관중재"], 27, ["hvangioayn", "hv32"], ""),
    ("패혈증 / 패혈성 쇼크", ["패혈증", "sepsis", "septic shock", "쇼크"], 28, ["hvicc", "hvventiayn"], "중환자실·인공호흡기 가용 기관"),
    ("급성 호흡부전 (인공호흡기 필요)", ["호흡부전", "respiratory failure", "ards", "인공호흡기", "삽관", "intubation", "폐렴 중증"], 28, ["hvicc", "hvventiayn"], "중환자실·인공호흡기 가용 기관"),
    ("심정지 후 소생 / 심인성 쇼크", ["심정지", "cardiac arrest", "rosc", "심인성 쇼크", "cardiogenic", "ecmo"], 28, ["hvicc", "hvecmoayn", "hvangioayn"], "ECMO·저체온치료 가능 기관"),
    ("폐색전증 (고위험)", ["폐색전", "pulmonary embolism", "pe"], 28, ["hvicc", "hvctayn"], "혈전용해/카테터 치료 가능 기관"),
    ("당뇨병성 케톤산증 / 고삼투압", ["dka", "케톤산증", "hhs", "고삼투압"], 28, ["hvicc"], ""),
    ("중증 외상 (다발성·쇼크·두부)", ["외상", "trauma", "다발성 외상", "추락", "교통사고", "tbi"], None, ["hv9", "hv60", "hvoc"], "권역외상센터 이송 고려"),
    ("급성 폐쇄성 요로감염 / 요로결석 폐쇄", ["신우신염", "요로결석", "수신증", "폐쇄성 신우신염"], 28, ["hvicc"], "비뇨의학과 응급 배액(PCN/스텐트) 가능 기관"),
]


def search_catalog(query: str) -> list[int]:
    """진단명/별칭에 query가 포함된 카탈로그 인덱스 목록."""
    q = (query or "").strip().lower()
    if not q:
        return []
    out = []
    for i, (name, aliases, *_rest) in enumerate(DIAGNOSIS_CATALOG):
        if q in name.lower() or any(q in a.lower() or a.lower() in q for a in aliases):
            out.append(i)
    return out


def catalog_dx(idx: int, score: int = 100) -> Dx:
    name, _aliases, cat, res, note = DIAGNOSIS_CATALOG[idx]
    return Dx(name, cat, score, list(res), "즉시", ["의사 직접 입력"], note)
