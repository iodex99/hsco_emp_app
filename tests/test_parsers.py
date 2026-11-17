"""Importer detection/parsing on synthetic files (always) and on the real reference exports (when present)."""
from datetime import date, datetime

import pytest

from app.importers import parsers as P
from app.importers.readers import read_path, read_workbook
from tests.conftest import HAS_REF, REF


def test_value_parsers():
    assert P.parse_hhmm("08 : 29") == 509
    assert P.parse_hhmm("186:38") == 186 * 60 + 38
    assert P.parse_hhmm("00:00") == 0
    assert P.parse_hhmm(8.5) == 510
    assert P.parse_dt("01 Oct 2026 10:00") == datetime(2026, 10, 1, 10, 0)
    assert P.parse_dt("31 Oct 2026 21:57:28") == datetime(2026, 10, 31, 21, 57, 28)
    assert P.parse_date("10 SEP 2026") == date(2026, 9, 10)
    assert P.parse_date(46296.0) == date(2026, 10, 1)
    assert P.emp_id(8.0) == "8" and P.emp_id(" hs001 ") == "HS001"
    assert P.norm("Employee  No") == "employee no" and P.norm("In Time  [Asia/Kolkata]") == "in time"


def test_detect_and_parse_synthetic(fixtures):
    s = read_path(fixtures["muster"])
    d = P.detect(s)
    assert [x.kind for x in d] == ["muster"] and d[0].sheet == "OG"
    assert P.detect_period(d[0], s["OG"]) == (2026, 10)
    rows, warn = P.parse_muster(s["OG"], d[0])
    assert not warn and {r.emp_no for r in rows} == {"T001", "T002", "T003", "T004", "T005", "T006"}
    sw = read_path(fixtures["swipes"])
    ds = P.detect(sw)
    assert ds[0].kind == "swipes"
    swipes, names, _ = P.parse_swipes(sw[ds[0].sheet], ds[0])
    assert names["T004"] == "Dev Mehta" and any(x.location_type == "Work from Home" for x in swipes)
    dj = read_path(fixtures["doj"])
    dd = P.detect(dj)
    assert dd[0].kind == "doj"
    recs = P.parse_doj(dj[dd[0].sheet], dd[0])
    assert len(recs) == 7 and recs[-1]["emp_no"] == "T007"
    mm = read_path(fixtures["master"])
    dm = P.detect(mm)
    assert dm[0].kind == "employee_master"
    m = P.parse_employee_master(mm[dm[0].sheet], dm[0])
    assert m[5]["weekly_off"] == "ALL_SAT_OFF"


def test_unrecognised_and_bad_files():
    with pytest.raises(ValueError):
        read_workbook(b"hello", "x.pdf")
    with pytest.raises(ValueError):
        read_workbook(b"PK\x03\x04garbage", "x.xlsx")
    assert P.detect({"s": [["a", "b"], [1, 2]]}) == []


def test_coordinates_detected():
    rows = [["Employee No", "Swipe Date", "Location Type", "Swipe Type", "Sign In Location"],
            ["E1", "01 Oct 2026 10:00:00", "Client Location", "Mobile Sign In", "19.11446, 72.8712"],
            ["E1", "01 Oct 2026 19:00:00", "Client Location", "Mobile Sign In", "no gps"]]
    ds = P.detect({"s": rows})[0]
    sw, _, _ = P.parse_swipes(rows, ds)
    assert (sw[0].lat, sw[0].lon) == (19.11446, 72.8712) and sw[1].lat is None
    rows2 = [["Employee No", "Swipe Date", "Latitude", "Longitude"], ["E1", "01 Oct 2026 10:00:00", 28.6331, 77.2211]]
    sw2, _, _ = P.parse_swipes(rows2, P.detect({"s": rows2})[0])
    assert sw2[0].lat == 28.6331


def test_offline_geocoder():
    from app.services.geo import offline_name
    assert "Andheri" in offline_name(19.1145, 72.8712)
    assert "Connaught" in offline_name(28.6331, 77.2211, 2) or "Delhi" in offline_name(28.6331, 77.2211, 2)
    assert offline_name(0.0, 0.0) is None


@pytest.mark.skipif(not HAS_REF, reason="reference/ (real data, git-ignored) not present")
def test_real_exports_detected():
    expect = {"Attendance Muster Report.xlsx": {"muster"}, "EmployeeSwipeDetails.xlsx": {"swipes"},
              "Attendance Summary Report.xls": {"summary"}, "DateOfJoiningReport.xlsx": {"doj"},
              "MIS Report October 2026_Manual.xlsx": {"mis_manual"}}
    for f, kinds in expect.items():
        s = read_path(REF / f)
        assert {d.kind for d in P.detect(s)} == kinds, f
    # wrong <dimension> tag in DOJ file must not truncate columns
    s = read_path(REF / "DateOfJoiningReport.xlsx")
    d = P.detect(s)[0]
    assert len(P.parse_doj(s[d.sheet], d)) == 101
    # 1,048,550-row used range is trimmed
    s = read_path(REF / "MIS Report October 2026_Manual.xlsx")
    assert len(s["Reference"]) < 200
