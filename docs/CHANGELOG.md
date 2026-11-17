# Changelog

## 1.0.0 — 2026-11-17 — first complete version
**Analysis**
- All 5 reference workbooks (every sheet, formula, pivot) and the HR policy PDF analysed → `docs/DATA_ANALYSIS.md`.
  Found: unanchored VLOOKUP and `LEFT(…,3)` bugs in the manual MIS, greytHR Summary week-days wrong for many,
  per-employee weekly-off/holiday/hours exceptions, HS207 holiday-hours inconsistency, 8 h/day for HS138/HS192.

**Application**
- Importers with auto-detection: greytHR muster, swipe details, attendance summary (.xls), DOJ; manual MIS
  (reconciliation + master seeding); templates for employee master, holidays, leave opening balances.
- Engine: policy rules (8.5 h × days, ½ day per 4 h short, exempt groups/employees, off-day/holiday hours excluded,
  comp-off ≥ 5 h, WFH ≤ 2), weekly-off patterns incl. custom weekdays, holiday calendars, status mapping, half days.
- Leave ledger (FY Apr–Mar, 15 days pro-rata, absence/shortfall debits, salary deduction when exhausted, encashment
  at year end) and comp-off ledger.
- 25 flag types, board KPIs, charts, top lists, group table, people movement, trends, per-employee history.
- Manual entries: day corrections, month figure overrides, opening balances, employee master edits — reason mandatory,
  audit-logged, highlighted in Excel.
- Period finalise/lock and admin re-open with reason.
- Reconciliation vs manual MIS and vs greytHR summary.
- Excel workbook (11 sheets, live formulas) and standalone interactive HTML board pack with drill-down.
- Location names: geofences, offline GeoNames gazetteer, optional OSM (off).
- Security: argon2 passwords, lockout, server-side sessions, CSRF, CSP/HSTS, roles, mandatory 2FA with TOTP,
  passkeys/security keys (WebAuthn), email codes, recovery codes; audit log.
- Windows installer (venv, HTTPS cert, scheduled tasks for server + nightly backup, firewall), CLI tools.
- Tests: 64 (unit, parsers, real-data reconciliation, HTTP integration, browser e2e incl. passkey).

**Defaults chosen from the data** (see PROJECT_CONTEXT decisions): LOP under Leaves; persistent late = > 60 min on
≥ 10 days; WFH distinct days; extra working day = any punch on an off-day.
