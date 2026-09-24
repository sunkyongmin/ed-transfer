"""streamlit 없이 app.py를 실행해 런타임 오류를 잡는 스모크 테스트 (개발용)."""
import sys, types, runpy
import test_nemc_api as T
import nemc_api as api

# --- streamlit 스텁 -------------------------------------------------------
class _Any:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, n): return _Any()
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter([_Any(), _Any(), _Any(), _Any()])
    def __getitem__(self, i): return _Any()

class _Stop(Exception): pass

st = types.ModuleType("streamlit")
calls = {}
def _rec(name, ret):
    def f(*a, **k):
        calls.setdefault(name, []).append((a, k)); return ret
    return f
def selectbox(label, options, **k): return options[SEL.get(label, 0)]
def multiselect(label, options, default=None, **k): return MS.get(label, default or [])
def radio(label, options, index=0, **k): return options[index]
SEL, MS = {}, {}
st.set_page_config = _rec("cfg", None)
st.sidebar = _Any(); st.title = _rec("title", None); st.caption = _rec("caption", None)
st.text_input = lambda *a, **k: "DUMMYKEY"
st.divider = _rec("div", None); st.subheader = _rec("sub", None)
st.selectbox = selectbox; st.multiselect = multiselect; st.radio = radio
st.number_input = lambda *a, value=0, **k: value
st.slider = lambda *a, **k: a[3] if len(a) > 3 else k.get("value", 15)
st.checkbox = lambda *a, value=False, **k: value
st.columns = lambda n, **k: [_Any() for _ in range(n if isinstance(n, int) else len(n))]
st.button = lambda *a, **k: False
st.info = _rec("info", None); st.warning = _rec("warning", None); st.error = _rec("error", None)
def stop(): raise _Stop()
st.stop = stop
st.spinner = lambda *a, **k: _Any()
st.metric = _rec("metric", None); st.markdown = _rec("md", None); st.write = _rec("write", None)
st.dataframe = _rec("dataframe", None); st.map = _rec("map", None)
st.tabs = lambda names: [_Any() for _ in names]
st.column_config = _Any()
class _CD:
    def __call__(self, **k):
        def deco(f):
            f.clear = lambda: None; return f
        return deco
    clear = staticmethod(lambda: None)
st.cache_data = _CD()
class _Secrets(dict):
    pass
st.secrets = _Secrets()
sys.modules["streamlit"] = st

# --- API 스텁: 샘플 XML 반환 ---------------------------------------------------
def fake_call(key, op, params, **k):
    xml = {"getEgytListInfoInqire": T.LIST_XML, "getEmrrmRltmUsefulSckbdInfoInqire": T.BEDS_XML,
           "getSrsillDissAceptncPosblInfoInqire": T.SEVERE_XML, "getEmrrmSrsillDissMsgInqire": T.MSG_XML,
           "getEgytBassInfoInqire": T.LIST_XML}[op]
    return api.parse_response(xml)[0]
api.call = fake_call

def run(label):
    calls.clear()
    try:
        runpy.run_path("app.py", run_name="__main__")
    except _Stop:
        pass
    df_calls = calls.get("dataframe", [])
    assert df_calls, f"{label}: dataframe 미출력 {calls.get('warning')}{calls.get('error')}"
    print(label, "→ 후보표 행 수:", len(df_calls[0][0][0]), "| 순위:", list(df_calls[0][0][0]["병원"]))

run("기본(중증질환 없음)")
SEL["중증질환 분류 (수용가능 여부 확인)"] = 3      # 거미막하출혈
run("거미막하출혈")
SEL["중증질환 분류 (수용가능 여부 확인)"] = 1
MS["필요 장비 (가용 'Y'인 병원 우선)"] = ["hvmriayn"]
MS["기관 등급"] = []
run("심근경색+MRI")
print("smoke test OK")
