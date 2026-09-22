"""활용가이드의 샘플 응답으로 파서·병합·정렬 로직 검증. 실행: python -m pytest -q 또는 python test_nemc_api.py"""
import nemc_api as api

LIST_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
<body><items>
<item><dutyAddr>서울특별시 종로구 평동 164</dutyAddr><dutyEmcls>G007</dutyEmcls><dutyEmclsName>지역응급의료기관</dutyEmclsName><dutyName>서울적십자병원</dutyName><dutyTel1>02-2002-8000</dutyTel1><dutyTel3>02-2002-0000</dutyTel3><hpid>A0000191</hpid><rnum>1</rnum><wgs84Lat>37.56740267694248</wgs84Lat><wgs84Lon>126.96704997103927</wgs84Lon></item>
<item><dutyAddr>서울특별시 종로구 평동 108</dutyAddr><dutyEmcls>G006</dutyEmcls><dutyEmclsName>지역응급의료센터</dutyEmclsName><dutyName>강북삼성병원</dutyName><dutyTel1>02-2001-2114</dutyTel1><dutyTel3>02-2001-1000</dutyTel3><hpid>A0000190</hpid><rnum>2</rnum><wgs84Lat>37.5685066114755</wgs84Lat><wgs84Lon>126.9678441210582</wgs84Lon></item>
<item><dutyAddr>서울특별시 종로구 연건동</dutyAddr><dutyEmcls>G001</dutyEmcls><dutyEmclsName>권역응급의료센터</dutyEmclsName><dutyName>서울대학교병원</dutyName><dutyTel1>02-2072-2114</dutyTel1><dutyTel3>02-2072-0000</dutyTel3><hpid>A0000005</hpid><rnum>3</rnum><wgs84Lat>37.57976260966429</wgs84Lat><wgs84Lon>126.99897999722211</wgs84Lon></item>
</items><numOfRows>30</numOfRows><pageNo>1</pageNo><totalCount>3</totalCount></body></response>"""

BEDS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header><body><items>
<item><dutyName>강북삼성병원</dutyName><hpid>A0000190</hpid><hvec>14</hvec><hvs01>28</hvs01><hvoc>9</hvoc><hvs22>15</hvs22><hvicc>3</hvicc><hvs17>10</hvs17><hvctayn>Y</hvctayn><hvmriayn>Y</hvmriayn><hvangioayn>Y</hvangioayn><hvventiayn>Y</hvventiayn><hvidate>20230414092700</hvidate></item>
<item><dutyName>서울대학교병원</dutyName><hpid>A0000005</hpid><hvec>-3</hvec><hvs01>40</hvs01><hvctayn>Y</hvctayn><hvmriayn>N</hvmriayn><hvidate>20230414093000</hvidate></item>
<item><dutyName>서울적십자병원</dutyName><hpid>A0000191</hpid><hvec>5</hvec><hvs01>10</hvs01><hvctayn>Y</hvctayn><hvmriayn>N</hvmriayn><hvangioayn>N</hvangioayn></item>
</items><numOfRows>10</numOfRows><pageNo>1</pageNo><totalCount>3</totalCount></body></response>"""

SEVERE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header><body><items>
<item><dutyName>강북삼성병원</dutyName><hpid>A0000190</hpid><MKioskTy1>Y </MKioskTy1><MKioskTy3>불가능</MKioskTy3><MKioskTy10>정보미제공</MKioskTy10><MKioskTy10Msg>1day</MKioskTy10Msg></item>
<item><dutyName>서울대학교병원</dutyName><hpid>A0000005</hpid><mkioskty1>Y</mkioskty1><mkioskty3>Y</mkioskty3></item>
<item><dutyName>서울적십자병원</dutyName><hpid>A0000191</hpid><MKioskTy1>N</MKioskTy1></item>
</items><numOfRows>30</numOfRows><pageNo>1</pageNo><totalCount>3</totalCount></body></response>"""

MSG_XML = """<?xml version="1.0" encoding="UTF-8" standalone="true"?>
<response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header><body><items>
<item><dutyAddr>서울특별시 종로구</dutyAddr><dutyName>서울대학교병원</dutyName><emcOrgCod>A0000005</emcOrgCod><hpid>A0000005</hpid><rnum>1</rnum><symBlkMsg>장비부족</symBlkMsg><symBlkMsgTyp>중증</symBlkMsgTyp><symBlkSttDtm>20170310173133</symBlkSttDtm><symTypCod>Y0031</symTypCod><symTypCodMag>[뇌출혈수술] 거미막하출혈</symTypCodMag></item>
</items><numOfRows>1</numOfRows><pageNo>1</pageNo><totalCount>1</totalCount></body></response>"""

GATEWAY_ERR = """<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"""


def _build():
    items, total = api.parse_response(LIST_XML)
    assert total == 3 and len(items) == 3
    hospitals = {}
    for it in items:
        hospitals[it["hpid"]] = api.Hospital(
            hpid=it["hpid"], name=it["dutyName"], emcls=it["dutyEmcls"], emcls_name=it["dutyEmclsName"],
            addr=it["dutyAddr"], tel_main=it["dutyTel1"], tel_er=it["dutyTel3"],
            lat=float(it["wgs84Lat"]), lon=float(it["wgs84Lon"]))
    beds = {it["hpid"]: it for it, in [(x,) for x in api.parse_response(BEDS_XML)[0]]}
    severe = {it["hpid"]: it for it in api.parse_response(SEVERE_XML)[0]}
    msgs = {}
    for it in api.parse_response(MSG_XML)[0]:
        msgs.setdefault(it["hpid"], []).append(it)
    origin = (37.5733, 126.9289)  # 동신병원
    return api.merge(hospitals, beds, severe, msgs, origin)


def test_gateway_error():
    try:
        api.parse_response(GATEWAY_ERR)
        assert False
    except api.NEMCError as e:
        assert "30" in str(e)


def test_merge_and_status():
    hs = {h.hpid: h for h in _build()}
    kb = hs["A0000190"]
    assert kb.bed("hvec") == 14 and kb.bed("hvs01") == 28
    assert kb.equip("hvmriayn") == "Y"
    assert api.severe_status(kb, 1) == "가능"       # "Y " 공백 처리
    assert api.severe_status(kb, 3) == "불가"       # "불가능"
    assert api.severe_status(kb, 10) == "정보없음"
    assert kb.severe_msg[10] == "1day"
    snu = hs["A0000005"]
    assert api.severe_status(snu, 3) == "가능"      # 소문자 mkioskty
    assert api.blocked_for(snu, 3) and "장비부족" in api.blocked_for(snu, 3)[0]
    assert not api.blocked_for(snu, 1)
    assert snu.bed("hvec") == -3                    # 음수(과밀) 그대로 유지
    assert 0 < kb.distance_km < 5


def test_ranking():
    hs = _build()
    # 거미막하출혈(3): 강북삼성 불가, 서울대 가능이지만 차단메시지 → 둘 다 뒤, 적십자 정보없음이 1위
    order = [h.name for h in sorted(hs, key=lambda h: api.rank_key(h, 3, [], 1))]
    assert order[0] == "서울적십자병원"
    # 심근경색(1): 강북삼성 Y, 서울대 Y(응급실 -3 → 병상 감점), 적십자 N
    order = [h.name for h in sorted(hs, key=lambda h: api.rank_key(h, 1, [], 1))]
    assert order == ["강북삼성병원", "서울대학교병원", "서울적십자병원"]
    # MRI 필요 시 서울대(N)는 강북삼성 뒤
    order = [h.name for h in sorted(hs, key=lambda h: api.rank_key(h, None, ["hvmriayn"], 1))]
    assert order[0] == "강북삼성병원"
    # 거리만
    order = [h.name for h in sorted(hs, key=lambda h: api.rank_key(h, 1, [], 1, "distance_only"))]
    assert order[-1] == "서울대학교병원"


if __name__ == "__main__":
    test_gateway_error(); test_merge_and_status(); test_ranking()
    print("all tests passed")
