"""Generate synthetic greytHR-format exports (fake people) so tests run without the real, git-ignored data.

Formats mirror the real October-2026 exports exactly (title rows, header names, value formats) — see docs/DATA_ANALYSIS.md.
Scenario (October 2026; 2-Oct & 20-Oct holidays for some):
  T001 Asha Rao      BSS      — std pattern, 3 h short  → 0.5 day deduction
  T002 Bilal Khan    ATA      — works 1st Sat + a Sunday (off-day work, comp-off), 6.1 h short → 1 day
  T003 Chitra Iyer   Partner  — short but exempt
  T004 Dev Mehta     Article  — 4 WFH days (over limit), persistently > 1 h late
  T005 Esha Patel    BSS      — 1 PL, 1 LOP, 1 absent (A), half-day PL
  T006 Farhan Ali    Offshore — all Saturdays off, joined 13-Oct-2026 (new joinee), missing punches, regularisation
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook

Y, M = 2026, 10
DAYS = [date(Y, M, d) for d in range(1, 32)]
EMPS = [
    ("T001", "Asha Rao", "BSS"), ("T002", "Bilal Khan", "ATA"), ("T003", "Chitra Iyer", "Partner"),
    ("T004", "Dev Mehta", "Article"), ("T005", "Esha Patel", "BSS"), ("T006", "Farhan Ali", "Offshore"),
]
DOJ = {"T001": date(2023, 4, 3), "T002": date(2024, 7, 1), "T003": date(2001, 1, 2), "T004": date(2025, 10, 6),
       "T005": date(2022, 10, 17), "T006": date(2026, 10, 13)}
HOLIDAYS = {date(2026, 10, 20)}
# synthetic coordinates (public landmarks, no real homes): office = Marine Lines, client = BKC
OFFICE = (18.93860, 72.82950)
CLIENT = (19.06600, 72.86800)
HOME_T004 = (19.11450, 72.87120)          # Andheri East
AWAY = (18.98440, 72.83970)               # Kalachowki — tagged "Office" but not at the office
DEVICE = {"T001": ("iPhone", "a1f0c0de00000001"), "T002": ("OnePlus 11R", "a1f0c0de00000002"),
          "T004": ("Redmi Note 13", "a1f0c0de00000004"), "T005": ("OPPO A57", "a1f0c0de00000005"),
          "T006": ("Galaxy M14", "a1f0c0de00000006")}


def _first_sat(d):
    return d.weekday() == 5 and d.day <= 7


def _is_off(emp, d):
    if d.weekday() == 6:
        return True
    if d.weekday() == 5:
        return not (_first_sat(d) and emp != "T006")
    return False


def build_days():
    """{emp: [(date, s1, s2, in_dt, out_dt, minutes, swipes[(dt, door, loc, type)])]}"""
    rnd = random.Random(42)
    out = {}
    for emp, name, grp in EMPS:
        rows = []
        for d in DAYS:
            if d < DOJ[emp]:
                continue
            s = "OFF" if _is_off(emp, d) else "H" if d in HOLIDAYS else "P"
            s1 = s2 = s
            mins = 0
            in_dt = out_dt = None
            swipes = []
            work = s == "P"
            # scenario tweaks
            if emp == "T005" and d.day == 7:
                s1 = s2 = "PL"; work = False
            if emp == "T005" and d.day == 8:
                s1 = s2 = "LOP"; work = False
            if emp == "T005" and d.day == 9:
                s1 = s2 = "A"; work = False
            if emp == "T005" and d.day == 14:
                s1, s2 = "PL", "P"
            if emp == "T002" and d.day == 11:           # works a Sunday 6 h
                work = True
            if work:
                if emp == "T004":
                    start = datetime(d.year, d.month, d.day, 11, 5) + timedelta(minutes=rnd.randint(0, 20))
                else:
                    start = datetime(d.year, d.month, d.day, 9, 35) + timedelta(minutes=rnd.randint(0, 24))
                length = timedelta(hours=8, minutes=30)
                if emp == "T001":
                    length = timedelta(hours=8, minutes=21)          # 9 min short/day
                if emp == "T002":
                    length = timedelta(hours=8, minutes=12)
                if emp == "T003":
                    length = timedelta(hours=7, minutes=30)
                if emp == "T005" and d.day == 14:
                    length = timedelta(hours=4, minutes=30)
                if emp == "T002" and d.day == 11:
                    length = timedelta(hours=6)
                in_dt, out_dt = start, start + length
                mins = int((out_dt - in_dt).total_seconds() // 60)
                if emp == "T003":
                    pass  # partner: no punches (auto attendance) — handled below
                elif emp == "T004" and d.day in (5, 6, 12, 13):
                    swipes = [(in_dt, "Mobile Sign In", "Work from Home", "Mobile Sign In", HOME_T004, ""),
                              (out_dt, "Mobile Sign In", "Work from Home", "Mobile Sign In", HOME_T004, "")]
                elif emp == "T002" and d.day in (28, 29):     # mobile "Office" sign-ins that ARE at the office
                    swipes = [(in_dt, "Mobile Sign In", "Office", "Mobile Sign In", OFFICE, ""),
                              (out_dt, "hasmukhshah", "", "Astra")]
                elif emp == "T005" and d.day in (28, 29):     # tagged "Office" from somewhere else
                    swipes = [(in_dt, "Mobile Sign In", "Office", "Mobile Sign In", AWAY, "Shift In"),
                              (out_dt, "hasmukhshah", "", "Astra")]
                elif emp == "T006" and d.day in (15, 22):
                    swipes = [(in_dt, "hasmukhshah", "", "Astra")]                      # missing out punch
                elif emp == "T006" and d.day in (16, 23, 27):
                    swipes = [(in_dt, "", "", "Permission", None, "forget to sign in"), (out_dt, "hasmukhshah", "", "Astra")]
                elif emp == "T001" and d.day in (21, 22):
                    swipes = [(in_dt, "Mobile Sign In", "Client Location", "Mobile Sign In", CLIENT, "At Acme Pharma (BKC)"),
                              (out_dt, "Mobile Sign In", "Client Location", "Mobile Sign In", CLIENT, "")]
                else:
                    swipes = [(in_dt + timedelta(seconds=rnd.randint(0, 59)), "hasmukhshah", "", "Astra"),
                              (out_dt + timedelta(seconds=rnd.randint(0, 59)), "hasmukhshah", "", "Astra")]
                if emp == "T003":
                    in_dt = datetime(d.year, d.month, d.day, 10, 0)
                    out_dt = datetime(d.year, d.month, d.day, 18, 30)
                    mins = 449
                if emp == "T002" and d.day == 11:
                    s1 = s2 = "OFF"
            rows.append((d, s1, s2, in_dt, out_dt, mins, swipes))
        out[emp] = rows
    return out


def _fmt_dt(dt):
    return dt.strftime("%d %b %Y %H:%M") if dt else None


def write_all(folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    days = build_days()
    names = {e: n for e, n, _ in EMPS}
    # ---- muster
    wb = Workbook()
    ws = wb.active
    ws.title = "OG"
    ws.append(["Test Firm LLP"]); ws.append(["1 Test Street, Mumbai"])
    ws.append(["Attendance Muster Roll From  01 Oct 2026 To 31 Oct 2026"])
    ws.append(["SNO", "Name", "Employee  No", "Attendance Date", "Day", "Session1  Status", "Session2  Status",
               "In Time  [Asia/Kolkata]", "Out Time  [Asia/Kolkata]", "Shift Name", "Total Work Hours"])
    n = 0
    for emp, _, _ in EMPS:
        for d, s1, s2, i, o, mins, _sw in days[emp]:
            n += 1
            ws.append([n, names[emp], emp, datetime(d.year, d.month, d.day), d.strftime("%A"), s1, s2, _fmt_dt(i), _fmt_dt(o),
                       "10:am-18:30pm", f"{mins // 60:02d} : {mins % 60:02d}"])
    wb.create_sheet("Extra Working Days").append(["Row Labels", "Count of Adjusted Extra Hours"])  # manual pivot noise
    p_muster = folder / "Attendance Muster Report.xlsx"
    wb.save(p_muster)
    # ---- swipes (newest first like greytHR)
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet0"
    # greytHR's current "Employee Swipe Details" layout (Nov-2026 sample): Longitude BEFORE Latitude, phone, remarks
    ws.append(["Employee No", "Employee Name", "Access Card", "Shift", "Swipe Date", "Door/Address", "Longitude",
               "Latitude", "Location Type", "Mobile Device Name", "Mobile Device Id", "Swipe Type", "Status", "Remarks",
               "Permission Reason", "Signed In/Out By", "Received On"])
    allsw = []
    for emp, _, _ in EMPS:
        for d, *_rest, sw in days[emp]:
            for t, door, loc, typ, *extra in sw:
                gps, note = (extra + [None, ""])[:2]
                allsw.append((t, emp, door, loc, typ, gps, note))
    # a rejected mobile sign-in (must be ignored by the calculation)
    allsw.append((datetime(2026, 10, 30, 23, 50), "T006", "Mobile Sign In", "Work from Home", "Mobile Sign In",
                  (19.2, 72.97), "late entry", "Rejected"))
    for t, emp, door, loc, typ, gps, note, *st in sorted(allsw, key=lambda x: x[0], reverse=True):
        ts = t.strftime("%d %b %Y %H:%M:%S")
        mobile = typ != "Astra"
        dev = DEVICE.get(emp, ("", "")) if typ == "Mobile Sign In" else ("", "")
        reason = note if typ == "Permission" else ""
        remark = "" if typ == "Permission" else note
        status = st[0] if st else ("" if typ == "Permission" else "Approved")
        ws.append([emp, names[emp], None if mobile else emp[1:], "10:am-18:30pm", ts, door,
                   f"{gps[1]:.8f}" if gps else "", f"{gps[0]:.8f}" if gps else "", loc, dev[0] or None, dev[1] or None,
                   typ, status, remark or None, reason, "", ts])
    wb.create_sheet("Sheet1").append(["Row Labels", "Count of Count"])
    p_swipes = folder / "EmployeeSwipeDetails.xlsx"
    wb.save(p_swipes)
    # ---- DOJ (active list; T006 included, plus a future joiner)
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet0"
    ws.append(["Date  Of  Joining  Report"])
    ws.append(["Employee Number", "Employee Name", "Joined On"])
    for emp, name, _ in EMPS:
        ws.append([emp, name, DOJ[emp].strftime("%d %b %Y").upper()])
    ws.append(["T007", "Gita Shah", "03 NOV 2026"])
    ws.append([None]); ws.append([f"Total number of records : {len(EMPS) + 1}"])
    p_doj = folder / "DateOfJoiningReport.xlsx"
    wb.save(p_doj)
    # ---- employee master
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Employee No", "Name", "Group", "Location", "Weekly Off Pattern", "Working Saturdays", "Hours Per Day",
               "Exempt From Deduction", "Exempt Reason", "Holiday Calendar", "Date of Joining", "Date of Leaving"])
    for emp, name, grp in EMPS:
        ws.append([emp, name, grp, "Mumbai", "ALL_SAT_OFF" if emp == "T006" else "MUSTER", None, None, None, None, None,
                   None, None])
    p_master = folder / "employee_master.xlsx"
    wb.save(p_master)
    return {"muster": p_muster, "swipes": p_swipes, "doj": p_doj, "master": p_master}


if __name__ == "__main__":
    import sys
    print(write_all(Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/out")))
