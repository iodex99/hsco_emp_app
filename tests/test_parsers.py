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


# ---------------------------------------------------------------- universal importer: layout-independent recognition

def _muster_rows(fixtures):
    s = read_path(fixtures["muster"])
    return s["OG"], P.detect(s)[0]


def test_columns_shuffled_and_renamed(fixtures):
    """Same data with columns in reverse order and renamed headers → identical parse."""
    rows, ds = _muster_rows(fixtures)
    base, _ = P.parse_muster(rows, ds)
    hr = ds.header_row
    rename = {"employee no": "Emp Code", "attendance date": "Date", "session1 status": "First Half",
              "session2 status": "Second Half", "in time": "Check-In", "out time": "Check-Out",
              "total work hours": "Hours Worked", "name": "Employee Name"}
    head = [rename.get(P.norm(h), h) for h in rows[hr]]
    shuffled = rows[:hr] + [list(reversed(head))] + [list(reversed(r)) for r in rows[hr + 1:]]
    d2 = P.detect({"x": shuffled})
    assert [x.kind for x in d2] == ["muster"] and not P.needs_review(d2[0])
    again, _ = P.parse_muster(shuffled, d2[0])
    key = lambda m: (m.emp_no, m.day, m.s1, m.s2, m.in_time, m.out_time, m.work_min)
    assert sorted(map(key, again)) == sorted(map(key, base))


def test_other_vendor_attendance_register():
    """A different system's register: single status column with words, separate time-only in/out, decimal hours."""
    rows = [["Monthly attendance register", "", "", "", "", ""],
            ["Period: 01-Oct-2026 to 31-Oct-2026", "", "", "", "", ""],
            [],
            ["Staff ID", "Staff Name", "Day", "Status", "Clock In", "Clock Out", "Worked (hrs)"],
            ["A12", "Asha", "2026-10-01", "Present", "09:58", "19:05", 9.12],
            ["A12", "Asha", "2026-10-02", "Week Off", "", "", ""],
            ["A12", "Asha", "2026-10-03", "Half Day", "10:00", "14:00", 4],
            ["A12", "Asha", "2026-10-05", "PL/P", "14:00", "19:00", "5h 0m"],
            ["B7", "Ravi", "02/10/2026", "Absent", "", "", ""],
            ["B7", "Ravi", "03/10/2026", "WFH", "21:00", "02:30", ""]]
    d = P.detect({"att": rows})
    assert [x.kind for x in d] == ["muster"]
    assert P.detect_period(d[0], rows) == (2026, 10)
    recs, warn = P.parse_muster(rows, d[0])
    by = {(r.emp_no, r.day.day): r for r in recs}
    assert by[("A12", 1)].s1 == "P" and by[("A12", 1)].work_min == 547
    assert by[("A12", 1)].in_time == datetime(2026, 10, 1, 9, 58)
    assert (by[("A12", 2)].s1, by[("A12", 2)].s2) == ("OFF", "OFF")
    assert (by[("A12", 3)].s1, by[("A12", 3)].s2) == ("P", "A")
    assert (by[("A12", 5)].s1, by[("A12", 5)].s2, by[("A12", 5)].work_min) == ("PL", "P", 300)
    assert by[("B7", 2)].s1 == "A"
    night = by[("B7", 3)]
    assert night.s1 == "WFH" and night.out_time == datetime(2026, 10, 4, 2, 30) and night.work_min == 330


def test_biometric_log_with_split_date_time_and_coordinates():
    rows = [["Device", "Date", "Time", "Direction", "User ID", "Name", "Lat", "Lng"],
            ["Astra-1", "01/10/2026", "09:41:10", "IN", "HS9", "Kiran", "18.9442", "72.8236"],
            ["Astra-1", "01/10/2026", "18:02:00", "OUT", "HS9", "Kiran", "", ""],
            ["Phone", "02/10/2026", "10:05", "IN", "HS9", "Kiran", "19.1145", "72.8712"]]
    d = P.detect({"log": rows})
    assert [x.kind for x in d] == ["swipes"]
    sw, names, _ = P.parse_swipes(rows, d[0])
    assert [s.ts for s in sw] == [datetime(2026, 10, 1, 9, 41, 10), datetime(2026, 10, 1, 18, 2), datetime(2026, 10, 2, 10, 5)]
    assert sw[0].door == "Astra-1" and sw[0].lat == 18.9442 and sw[1].lat is None and names["HS9"] == "Kiran"
    assert sw[0].swipe_type == "Punch IN"


def test_inferred_column_needs_review_and_manual_mapping():
    """No usable header for the employee column → it is inferred from values, flagged for review; a manual
    mapping then parses exactly and the layout can be recognised from memory."""
    rows = [["Col A", "Attendance Date", "Session1 Status", "Session2 Status"],
            ["HS001", "01 Oct 2026", "P", "P"], ["HS002", "01 Oct 2026", "A", "A"], ["HS003", "01 Oct 2026", "P", "PL"]]
    d = P.detect({"s": rows})
    assert d and d[0].kind == "muster" and "emp_no" in d[0].inferred and P.needs_review(d[0])
    ds = P.manual_sheet("s", rows, "muster", 0, {"emp_no": 0, "date": 1, "s1": 2, "s2": 3})
    recs, _ = P.parse_muster(rows, ds)
    assert [(r.emp_no, r.s2) for r in recs] == [("HS001", "P"), ("HS002", "A"), ("HS003", "PL")]
    lay = {"signature": P.layout_signature(rows[0]), "kind": "muster",
           "map": {"emp_no": "Col A", "date": "Attendance Date", "s1": "Session1 Status", "s2": "Session2 Status"}}
    d2 = P.detect({"s": rows}, [lay])
    assert d2[0].source == "layout" and not P.needs_review(d2[0]) and d2[0].map == ds.map
    assert P.missing_required("muster", {"emp_no": 0}) and not P.missing_required("muster", ds.map)


def test_value_normalisation():
    assert P.status_codes("PL/P") == ("PL", "P") and P.status_codes("Week Off") == ("OFF", "OFF")
    assert P.status_codes("present") == ("P", "P") and P.status_codes("LWP") == ("LWP", "LWP")
    assert P.parse_hhmm("8h 29m") == 509 and P.parse_hhmm("45m") == 45 and P.parse_hhmm(9.5) == 570
    assert P.parse_time(0.5) == datetime(2026, 1, 1, 12, 0).time() and P.parse_time("7:05 PM").hour == 19
    assert P.parse_date("2026-10-05") == date(2026, 10, 5) and P.parse_date("Oct 5, 2026") == date(2026, 10, 5)
    assert P.parse_dt("05/10/2026 7:05 PM") == datetime(2026, 10, 5, 19, 5)


def test_mapping_suggestions_from_values():
    """When headers are meaningless, the mapping screen is pre-filled from what the values look like."""
    rows = [["Monthly register"], [], ["Ref", "Dt", "AM", "PM", "Came", "Went"]] + \
        [["HS014", f"{d:02d}-10-2026", "P", "PL", "09:58", "19:05"] for d in range(1, 8)]
    assert P.suggest(rows, 2, "muster") == {"emp_no": 0, "date": 1, "s1": 2, "s2": 3, "in_time": 4, "out_time": 5}
    rows2 = [["A", "B", "C"], ["HS1", "01/10/2026", "Present"], ["HS2", "01/10/2026", "Week Off"], ["HS3", "02/10/2026", "Absent"]]
    assert P.suggest(rows2, 0, "muster") == {"emp_no": 0, "date": 1, "status": 2}
    assert P.guess_header_row(rows) == 2


# ---------------------------------------------------------------- regression tests for the v1.4 review findings

def test_numeric_employee_codes_are_not_dates():
    rows = [["Code", "Name", "Attendance Date", "Status"], [50123, "A", "01/10/2026", "P"], [50124, "B", "01/10/2026", "A"],
            [50125, "C", "02/10/2026", "P"]]
    d = P.detect({"s": rows})[0]
    assert d.kind == "muster" and d.map["emp_no"] == 0 and d.map["date"] == 2 and not P.needs_review(d)
    recs, _ = P.parse_muster(rows, d)
    assert [r.emp_no for r in recs] == ["50123", "50124", "50125"]
    # with an unhelpful date header the numeric code column is never inferred as the date
    rows2 = [["Code", "Name", "Col", "Status"]] + rows[1:]
    d2 = P.detect({"s": rows2})[0]
    assert d2.map.get("date") == 2 and P.needs_review(d2)


def test_status_words_and_codes():
    assert P.status_codes("Half Day Leave") == ("PL", "P") and P.status_codes("Half Day CL") == ("CL", "P")
    assert P.status_codes("Half Day Absent") == ("A", "P") and P.status_codes("Half Day") == ("P", "A")
    assert P.status_codes("P(HD)") == ("P", "A") and P.status_codes("PL (HD)") == ("PL", "P") and P.status_codes("hd") == ("P", "A")
    # short codes are kept exactly (an admin may map PH / AB / WO themselves)
    assert P.status_codes("PH") == ("PH", "PH") and P.status_codes("ab") == ("AB", "AB") and P.status_codes("WO") == ("WO", "WO")


def test_hours_and_time_edge_cases():
    from datetime import timedelta
    assert P.parse_hhmm(timedelta(days=1, hours=2, minutes=5)) == 1565 and P.parse_hhmm("1 day, 2:05:00") == 1565
    assert P.parse_time("9.5") is None and P.parse_time(8.5) is None and P.parse_time("9.30").minute == 30


def test_warning_rows_are_spreadsheet_rows():
    rows = [["Employee No", "Attendance Date", "Session1 Status", "Session2 Status"], ["E1", "01 Oct 2026", "P", "P"],
            [None, None, None, None], ["", "bad", "P", "P"], ["E2", "02 Oct 2026", "P", "P"]]
    _, warn = P.parse_muster(rows, P.detect({"s": rows})[0])
    assert warn == ["row 4: missing employee/date — skipped"]


def test_period_title_prefers_from_to_and_skips_print_dates():
    rows = [["Acme Ltd"], ["Printed 02-Nov-2026"], ["Attendance Register October 2026"],
            ["Employee No", "Attendance Date", "Session1 Status", "Session2 Status"], ["E1", "01 Oct 2026", "P", "P"]]
    d = P.detect({"s": rows})[0]
    assert P.detect_period(d, rows) == (2026, 10)


def test_remembered_layout_replay_is_validated():
    rows = [["Emp", "", "Date", "", "Status"], ["E1", "x", "01 Oct 2026", "y", "P"], ["E2", "x", "01 Oct 2026", "y", "A"]]
    lay = {"signature": P.layout_signature(rows[0]), "kind": "muster",
           "map": {"emp_no": {"i": 0, "h": "Emp"}, "name": {"i": 1, "h": ""}, "date": {"i": 2, "h": "Date"},
                   "status": {"i": 4, "h": "Status"}}}
    d = P.detect({"s": rows}, [lay])[0]
    assert d.source == "layout" and d.map == {"emp_no": 0, "name": 1, "date": 2, "status": 4}
    # same header set, columns moved → the layout no longer fits and is not used blindly
    moved = [["Status", "", "Date", "", "Emp"], ["P", "x", "01 Oct 2026", "y", "E1"], ["A", "x", "01 Oct 2026", "y", "E2"]]
    d2 = P.detect({"s": moved}, [lay])
    assert not d2 or d2[0].source != "layout"
    # an old-style layout (header text only) with a duplicate header is not replayed
    old = {"signature": P.layout_signature(rows[0]), "kind": "muster", "map": {"emp_no": "Emp", "date": "Date", "status": "Status", "name": ""}}
    d3 = P.detect({"s": rows}, [old])
    assert not d3 or d3[0].source != "layout"


def test_detection_is_thread_safe():
    import threading
    s = read_path(__import__("tests.fixtures.make_fixtures", fromlist=["x"]).write_all(
        __import__("pathlib").Path(__import__("tempfile").mkdtemp()))["muster"])
    errors = []

    def work():
        try:
            for _ in range(15):
                assert P.detect(s)[0].kind == "muster"
        except Exception as ex:  # noqa: BLE001
            errors.append(ex)
    ts = [threading.Thread(target=work) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
