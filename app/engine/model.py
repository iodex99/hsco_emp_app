"""Plain data types for the calculation engine (no DB / web dependencies)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Optional

WORKDAY = "WORKDAY"
WEEKLY_OFF = "WEEKLY_OFF"
HOLIDAY = "HOLIDAY"

# weekly-off patterns
P_MUSTER = "MUSTER"
P_STD_FIRST_SAT = "STD_FIRST_SAT"
P_ALL_SAT_OFF = "ALL_SAT_OFF"
P_SUN_ONLY = "SUN_ONLY"
P_CUSTOM = "CUSTOM"
PATTERNS = {
    P_MUSTER: "As per greytHR muster",
    P_STD_FIRST_SAT: "Sun off, Sat off except 1st Saturday (firm standard)",
    P_ALL_SAT_OFF: "All Saturdays & Sundays off",
    P_SUN_ONLY: "Only Sunday off (6-day week)",
    P_CUSTOM: "Custom: chosen weekly-off weekdays (+ working Saturdays)",
}
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# status buckets
B_PRESENT = "present"
B_ABSENT = "absent"
B_LEAVE = "leave"
B_COMPOFF = "compoff"
B_LOP = "lop"          # retired (v1.13, owner "remove LOP completely"): only in months calculated before v1.13
B_OFF = "off"
BUCKETS = (B_PRESENT, B_ABSENT, B_LEAVE, B_COMPOFF, B_OFF)

DEFAULT_STATUS_MAP = {
    "P": B_PRESENT, "WFH": B_PRESENT, "OD": B_PRESENT, "WO-P": B_PRESENT,
    "A": B_ABSENT,
    "PL": B_LEAVE, "CL": B_LEAVE, "SL": B_LEAVE, "EL": B_LEAVE, "ML": B_LEAVE, "L": B_LEAVE,
    "COF": B_COMPOFF, "CO": B_COMPOFF,
    "LOP": B_LEAVE, "LWP": B_LEAVE,   # loss of pay is leave granted in greytHR (v1.13: no separate LOP)
    "OFF": B_OFF, "H": B_OFF, "WO": B_OFF,
    "RH": B_OFF,          # restricted holiday the person took — an earned holiday (v1.12, see engine RH_CODES)
}
# greytHR status codes meaning "restricted holiday taken" (owner, v1.12): the day is the person's holiday — never an
# absence — and work done that day earns no comp-off. Handled in the engine whatever the status list says.
RH_CODES = ("RH",)


@dataclass
class Policy:
    hours_per_day: float = 8.5
    shortfall_block_hours: float = 4.0
    deduction_per_block_days: float = 0.5
    shortfall_grace_hours: float = 0.0
    exempt_groups: tuple = ("Partner", "SS", "Article", "Trainee")
    status_map: dict = field(default_factory=lambda: dict(DEFAULT_STATUS_MAP))
    comp_off_min_hours: float = 5.0
    wfh_limit_days: float = 2.0
    # Saturdays (nth of the month) the branches are open — owner, 19-Nov-2026: only the 1st. Someone whose working
    # Saturday falls on a closed one has to work from home: that WFH is listed but not counted towards the limit.
    # () = every Saturday open (no such exception).
    office_open_saturdays: tuple = (1,)
    wfh_location_types: tuple = ("work from home", "wfh")
    wfh_method: str = "distinct_days"     # or 'swipes_half'
    wfh_excess_deduction_days: float = 0.5   # leave deducted per WFH day above the limit (0 = flag only) — v1.6.5
    # groups never deducted for WFH over the limit (v1.6.6, owner: Article / SS / Trainee ARE deducted; exempt_groups
    # and the employee "exempt" flag concern the hours shortfall only)
    wfh_deduction_exempt_groups: tuple = ("Partner",)
    late_grace_min: int = 15
    very_late_min: int = 60
    early_exit_grace_min: int = 15
    persistent_late_days: int = 10
    persistent_late_basis: str = "very_late"   # 'very_late' (> very_late_min) or 'late' (> grace)
    persistent_regularisation_days: int = 3
    min_daily_hours: float = 7.0
    late_sitting_after: time = time(21, 0)     # not used since v1.16 (kept so older saved rules still read)
    # late sitting (owner, v1.16): a present working day with more than the person's required hours per day (their
    # minimum-hours policy / own / category / firm) plus this many minutes — not a fixed clock time
    late_sitting_extra_min: int = 60
    metrics_exclude_groups: tuple = ("Partner",)
    # employee groups the app ignores completely (owner, v1.13: "OTHERS … they dont concern us") — no results, no
    # metrics, no lists; their rows in uploads are kept but never used. Compared case-insensitively.
    ignored_groups: tuple = ("OTHERS",)
    default_shift_start: time = time(10, 0)
    default_shift_end: time = time(18, 30)
    shift_overrides: dict = field(default_factory=dict)   # shift name -> (start, end)
    # paid leave per financial year — only for "leave used" in Insights (balances are kept in greytHR, v1.9)
    annual_leave_days: float = 15.0
    fy_start_month: int = 4


@dataclass
class Employee:
    emp_no: str
    name: str = ""
    group: str = ""
    location: str = ""
    doj: Optional[date] = None
    dol: Optional[date] = None
    weekly_off: str = P_MUSTER
    off_weekdays: tuple = (5, 6)          # CUSTOM pattern: weekday numbers that are off (Mon=0..Sun=6)
    working_saturdays: tuple = ()         # CUSTOM pattern: nth Saturdays that are working even if Sat is off
    hours_per_day: Optional[float] = None  # None -> policy default
    exempt: bool = False
    exempt_reason: str = ""
    holidays: Optional[dict] = None        # {date: name} from assigned calendar, None = use muster
    optional_holidays: dict = field(default_factory=dict)   # {date: name} optional holidays of that calendar (v1.7)
    # weekly-off evidence from earlier months' muster for weekday slots this month cannot tell (e.g. the one 1st
    # Saturday is a holiday): {slot: True = weekly off} — see engine.greythr_weekly_offs (v1.14)
    off_slots_hint: dict = field(default_factory=dict)
    category: str = ""


@dataclass
class MusterDay:
    emp_no: str
    day: date
    s1: str = ""
    s2: str = ""
    in_time: Optional[datetime] = None
    out_time: Optional[datetime] = None
    work_min: int = 0
    shift: str = ""
    name: str = ""


@dataclass
class Swipe:
    emp_no: str
    ts: datetime
    door: str = ""
    location_type: str = ""
    swipe_type: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    place: str = ""
    note: str = ""            # the employee's remark on a mobile sign-in (e.g. client name)
    reason: str = ""          # permission / regularisation reason
    approval: str = ""        # Approved / Pending / Rejected … (rejected punches are ignored)
    device: str = ""          # phone model
    device_id: str = ""       # phone identifier (same phone used by several people = risk)


REJECTED_APPROVALS = ("rejected", "declined", "cancelled", "canceled", "denied")


@dataclass
class DayOverride:
    emp_no: str
    day: date
    day_type: Optional[str] = None
    s1: Optional[str] = None
    s2: Optional[str] = None
    work_min: Optional[int] = None
    reason: str = ""


@dataclass
class DayDetail:
    day: date
    weekday: str
    day_type: str
    s1: str
    s2: str
    b1: str
    b2: str
    in_time: Optional[datetime]
    out_time: Optional[datetime]
    work_min: int
    shift: str
    swipes: list = field(default_factory=list)
    location_types: list = field(default_factory=list)
    holiday_name: str = ""
    late_min: int = 0
    is_late: bool = False
    is_very_late: bool = False
    is_early_exit: bool = False
    is_short_day: bool = False
    is_late_sitting: bool = False
    is_regularised: bool = False
    is_wfh: bool = False
    is_partial_wfh: bool = False
    wfh_office_closed: bool = False   # WFH forced: Saturday the office is closed
    is_extra_work: bool = False
    compoff_earned: bool = False
    is_rh: bool = False                # restricted holiday (greytHR RH) — earned holiday, no comp-off (v1.12)
    is_sandwich: bool = False
    mobile_office_swipes: int = 0
    overridden: bool = False
    notes: list = field(default_factory=list)


@dataclass
class EmployeeMonth:
    emp_no: str
    name: str
    group: str
    location: str
    # MIS columns
    total_week_days: float = 0
    holidays: float = 0
    leave_days: float = 0
    absent_days: float = 0
    days_present: float = 0
    wfh_days: float = 0
    total_hours: float = 0
    extra_days: float = 0
    extra_hours: float = 0
    required_hours: float = 0
    actual_hours: float = 0
    balance_hours: float = 0
    shortfall_hours: float = 0
    deduction_days: float = 0
    exempt: bool = False
    remark: str = ""
    # splits
    pl_days: float = 0
    cof_days: float = 0
    unauth_absent_days: float = 0
    present_halves_days: float = 0
    hours_per_day: float = 8.5
    # behaviour metrics
    late_days: int = 0
    very_late_days: int = 0
    avg_late_min: float = 0
    early_exit_days: int = 0
    short_days: int = 0
    late_sitting_days: int = 0
    regularised_days: int = 0
    permission_swipes: int = 0
    partial_wfh_days: int = 0
    wfh_swipes: int = 0
    offday_wfh_days: int = 0      # WFH sign-ins on the person's weekly off / holiday: off-day work, not WFH
    leaveday_wfh_days: int = 0    # WFH sign-ins on a working day of full leave / absence: not WFH (v1.6.7)
    wfh_excess_days: float = 0    # WFH days above the monthly limit
    office_closed_wfh_days: int = 0   # WFH on a Saturday the office is closed (not counted towards the limit)
    wfh_approved_days: float = 0      # extra WFH days approved by a manager / partner (month correction, v1.7.5)
    shortfall_deduction_days: float = 0   # part of deduction_days for hours shortfall
    wfh_deduction_days: float = 0         # part of deduction_days for WFH above the limit
    wfh_deduction_exempt: bool = False    # not deducted for WFH (Partner-type group or month exception)
    client_days: int = 0
    onduty_days: int = 0
    mobile_office_swipes: int = 0
    total_swipes: int = 0
    biometric_swipes: int = 0
    mobile_swipes: int = 0
    compoff_earned: float = 0
    sandwich_count: int = 0
    avg_in_min: Optional[float] = None
    avg_out_min: Optional[float] = None
    avg_work_hours: Optional[float] = None
    auto_attendance: bool = False
    is_new_joinee: bool = False
    is_exit: bool = False
    overrides: dict = field(default_factory=dict)
    flags: list = field(default_factory=list)   # list of (code, severity, message)
    days: list = field(default_factory=list)

    def mis_row(self) -> dict:
        return {k: getattr(self, k) for k in (
            "total_week_days", "holidays", "leave_days", "absent_days", "days_present", "wfh_days",
            "total_hours", "extra_days", "extra_hours", "required_hours", "actual_hours",
            "balance_hours", "deduction_days")}
