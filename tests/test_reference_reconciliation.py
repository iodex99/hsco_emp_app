"""The decisive test: the engine reproduces HR's manual October-2026 MIS from the raw greytHR exports.

91 of 97 employees match every column. The remaining 6 differences are inconsistencies in the manual sheet itself
(documented in docs/DATA_ANALYSIS.md / docs/TESTING.md) and are asserted here explicitly so any regression shows up.
"""
import pytest

from tests.conftest import HAS_REF

pytestmark = pytest.mark.skipif(not HAS_REF, reason="reference/ (real data, git-ignored) not present")

KEYS = ["total_week_days", "holidays", "leave_days", "absent_days", "days_present", "wfh_days", "total_hours",
        "extra_days", "extra_hours", "required_hours", "actual_hours", "balance_hours"]

# emp -> set of fields allowed to differ, with the reason
KNOWN = {
    "HS053": ({"extra_days", "extra_hours", "actual_hours", "balance_hours"}, "HR removed 1st Sat from week days but kept its 8:55 h"),
    "HS162": ({"extra_days", "extra_hours", "actual_hours", "balance_hours"}, "same as HS053"),
    "HS192": ({"extra_days", "extra_hours", "actual_hours", "balance_hours"}, "same as HS053"),
    "HS068": ({"extra_days", "extra_hours", "actual_hours", "balance_hours"}, "HR counted 31-Oct as a week day yet excluded its hours"),
    "HS179": ({"leave_days", "days_present", "required_hours", "balance_hours", "wfh_days"}, "HR rounded a half-day leave; WFH distinct days 4 vs swipes/2 = 3"),
    "HS207": ({"extra_days", "extra_hours", "actual_hours", "balance_hours"}, "HR credited 16:58 auto-marked hours on 2 holidays"),
}


@pytest.fixture(scope="module")
def run():
    from app.engine.compute import compute_month
    from tests.reference_setup import DAY_OVERRIDES, MONTH_OVERRIDES, build
    b = build()
    res = compute_month(2026, 10, b["employees"], b["muster"], b["swipes"], b["policy"], DAY_OVERRIDES, MONTH_OVERRIDES)
    return b, res


def test_population(run):
    b, res = run
    assert b["period"] == (2026, 10)
    assert len(b["muster"]) == 2988 and len(b["swipes"]) == 4149
    assert len(res) == 97 == len(b["mis"])


def _hr(r, k):
    """The app's figure as HR's sheet would show it. HR counted WFH sign-ins on weekly offs / holidays as WFH; the app
    no longer does (owner, 2026-11-18 — BUSINESS_RULES §6), so those days are added back for the comparison."""
    return getattr(r, k) + (r.offday_wfh_days + r.leaveday_wfh_days + r.office_closed_wfh_days if k == "wfh_days" else 0)


def test_every_column_matches_manual_mis(run):
    b, res = run
    unexpected = []
    for emp, r in res.items():
        m = b["mis"][emp]
        allowed = KNOWN.get(emp, (set(), ""))[0]
        for k in KEYS:
            if abs((m.get(k) or 0) - _hr(r, k)) > 0.02 and k not in allowed:
                unexpected.append((emp, k, m.get(k), round(_hr(r, k), 2)))
    assert not unexpected, unexpected
    exact = [e for e in res if all(abs((b["mis"][e].get(k) or 0) - _hr(res[e], k)) <= 0.02 for k in KEYS)]
    assert len(exact) == 91


def test_wfh_on_own_off_days_not_counted(run):
    """October: 18 WFH sign-ins fell on the person's own weekly off or holiday (17 people). They are off-day work, carry
    a note, and no longer count as WFH — which takes HS020, HS045 and HS093 back within the 2-day limit."""
    b, res = run
    off = {e: r.offday_wfh_days for e, r in res.items() if r.offday_wfh_days}
    assert sum(off.values()) == 18 and len(off) == 17 and off["HS045"] == 2
    for e in off:
        days = [d for d in res[e].days if any("not counted as WFH (off-day work)" in n for n in d.notes)]
        assert len(days) == off[e] and all(d.day_type != "WORKDAY" and not d.is_wfh and d.is_extra_work for d in days)
    for e in ("HS020", "HS045", "HS093"):
        assert res[e].wfh_days <= 2 < res[e].wfh_days + res[e].offday_wfh_days
    # v1.6.7: HS045's 27-Oct is comp-off for the whole day in greytHR, yet has WFH sign-ins — not a WFH day
    assert {e: r.leaveday_wfh_days for e, r in res.items() if r.leaveday_wfh_days} == {"HS045": 1}
    d27 = next(d for d in res["HS045"].days if d.day.day == 27)
    assert not d27.is_wfh and d27.b1 == d27.b2 == "compoff" and any("day of leave / absence" in n for n in d27.notes)


def test_deductions_match_manual_remarks(run):
    b, res = run
    ded = {e: r.shortfall_deduction_days for e, r in res.items() if r.shortfall_deduction_days}
    # HR: HS112 "1 Day Deduction", HS138 & HS205 "0.5 Days deduction"; HS207 is the documented finding
    assert ded == {"HS112": 1.0, "HS138": 0.5, "HS205": 0.5, "HS207": 0.5}
    # v1.6.5 (owner): ½ day per WFH day above 2 — HR did not apply this in October's manual sheet
    wfh = {e: (r.wfh_days, r.wfh_deduction_days) for e, r in res.items() if r.wfh_deduction_days}
    assert wfh == {"HS179": (4, 1.0), "HS195": (3, 0.5)}
    # v1.7.4: HS016 worked from home on 4 Saturdays the office was closed (only the 1st Saturday is open) — listed,
    # not counted: 6 WFH days → 2, no deduction
    r16 = res["HS016"]
    assert (r16.wfh_days, r16.office_closed_wfh_days, r16.wfh_deduction_days) == (2, 4, 0)
    assert [d.day.day for d in r16.days if d.wfh_office_closed] == [10, 17, 24, 31]
    assert all(r.deduction_days == r.shortfall_deduction_days + r.wfh_deduction_days for r in res.values())
    # exempt groups / individuals produce no deduction even when short
    for e in ("HS001", "HS004", "HS011", "HS014", "HS055", "HS098", "HS126", "HS129", "HS158"):
        assert res[e].deduction_days == 0, e


def test_summary_total_hours_reconcile(run):
    b, res = run
    from app.importers.parsers import parse_hhmm
    for e, s in b["summary"].items():
        if e == "HS197":   # HR disallowed one day (day override)
            continue
        assert abs(parse_hhmm(s["total_hours"]) / 60 - res[e].total_hours) < 0.02, e


def test_behaviour_metrics_on_real_data(run):
    b, res = run
    wfh_over = sorted(e for e, r in res.items() if r.wfh_days > 2)
    assert wfh_over == ["HS179", "HS195"]          # HS020/HS045/HS093: their extra WFH was on off days
    assert sum(r.missing_punch_days for r in res.values()) > 0
    assert res["HS079"].days_present == 0 and any(f[0] == "NO_ATTENDANCE" for f in res["HS079"].flags)
    assert res["HS001"].auto_attendance  # partner: present all month, no punches
    assert res["HS055"].missing_punch_days >= 15  # one punch a day; greytHR auto-fills 10:00-18:30
    assert res["HS158"].no_punch_present_days == 18 and res["HS207"].no_punch_present_days == 15
    assert res["HS001"].late_days == 0   # partners excluded from behaviour metrics
