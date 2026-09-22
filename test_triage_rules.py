"""규칙 기반 진단추정 엔진 테스트. 실행: python test_triage_rules.py"""
import triage_rules as tr


def top(f):
    d = tr.evaluate(f)
    return d[0] if d else None


def test_stemi():
    f = tr.Findings(age=62, sex="M", cc={"흉통"}, ecg="ste", troponin_pos=True)
    d = top(f); assert d.category == 1 and "심근경색" in d.name and "hvangioayn" in d.resources


def test_stroke_free_text():
    f = tr.Findings(age=70, sex="F", free_text="1시간 전 발생한 좌측 편마비, brain CT 출혈 없음")
    hits = tr.apply_free_text(f)
    assert f.focal_deficit
    f.imaging.add("CT 정상(뇌)"); f.onset_hours = 1
    d = top(f); assert d.category == 2 and "뇌졸중" in d.name


def test_sah_beats_stroke():
    f = tr.Findings(age=55, cc={"두통"}, free_text="thunderclap headache, CT SAH")
    tr.apply_free_text(f)
    assert "CT 지주막하출혈" in f.imaging
    d = top(f); assert d.category == 3


def test_ich_excludes_ischemic():
    f = tr.Findings(age=68, focal_deficit=True, imaging={"CT 뇌출혈"})
    ds = tr.evaluate(f)
    assert ds[0].category == 4 and all(d.category != 2 for d in ds)


def test_dissection():
    f = tr.Findings(age=60, cc={"흉통", "요통/등통증"}, pulse_deficit=True, imaging={"CXR 종격동 확장"})
    d = top(f); assert d.category == 5


def test_cholangitis():
    f = tr.Findings(age=75, cc={"복통", "발열"}, bt=39.0, bilirubin=4.2, imaging={"CT/US 담관확장·담관결석"}, sbp=85)
    d = top(f); assert d.category == 8 and d.urgency == "즉시"


def test_ugib():
    f = tr.Findings(age=50, hematemesis_melena=True, hb=6.5, sbp=88, hr=125)
    d = top(f); assert d.category == 11 and d.urgency == "즉시"


def test_infant_intussusception():
    f = tr.Findings(age=0.8, cc={"복통"}, hematochezia=True, imaging={"US 장중첩"})
    d = top(f); assert d.category == 10


def test_hyperkalemia_esrd():
    f = tr.Findings(age=66, esrd=True, k=6.8, cc={"호흡곤란"}, pulm_edema=True)
    d = top(f); assert d.category == 22
    f.sbp = 80; f.lactate = 5
    d = top(f); assert d.category == 23


def test_ectopic():
    f = tr.Findings(age=29, sex="F", pregnant=True, gest_weeks=7, cc={"복통"}, vaginal_bleeding=True, sbp=85)
    d = top(f); assert d.category == 17


def test_burn():
    f = tr.Findings(age=40, burn_tbsa=25, burn_special=True)
    d = top(f); assert d.category == 19 and d.urgency == "즉시"


def test_amputation():
    f = tr.Findings(age=35, free_text="우측 2,3수지 절단")
    tr.apply_free_text(f)
    d = top(f); assert d.category == 20


def test_psych_eye():
    assert top(tr.Findings(age=30, psych="suicidal")).category == 24
    assert top(tr.Findings(age=30, eye_emergency="chemical")).category == 25


def test_nonspecific_sepsis():
    f = tr.Findings(age=80, cc={"발열"}, bt=39.2, wbc=18, sbp=82, lactate=4.5, spo2=88)
    d = top(f); assert d.category == 28 and "hvventiayn" in d.resources


def test_nothing():
    assert tr.evaluate(tr.Findings(age=30, cc={"설사"})) == []


if __name__ == "__main__":
    import sys
    g = dict(globals())
    n = 0
    for name, fn in g.items():
        if name.startswith("test_") and callable(fn):
            fn(); n += 1
    print(f"{n} triage tests passed")
