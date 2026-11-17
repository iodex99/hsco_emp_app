# HSCO Attendance & Leave Intelligence

On-premise web app for a CA firm that turns monthly **greytHR** exports into the **payroll leave calculation**
(hours shortfall → leave deduction, leave & comp-off ledgers, LOP) and **board-level employee metrics** — with
drill-down to every employee-day, manual corrections with an audit trail, Excel and interactive HTML reports, and
mandatory multi-method two-factor authentication. All data stays on the firm's Windows server.

![status](https://img.shields.io/badge/tests-66%20passing-brightgreen)

## What it does
- **Import** the month's greytHR exports as they are (filters, pivots and manual sheets are ignored automatically).
- **Calculate** per the firm's HR policy: required hours = days present × 8.5, excess offsets shortfall within the
  month, shortfall deducted in ½-day blocks per 4 h, holiday/off-day hours excluded, exemptions by group or person,
  per-employee weekly-off patterns and holiday calendars, comp-offs, WFH limit.
- **Flag** what needs action: deductions, unauthorised absence, WFH > 2 days, persistently > 1 h late, repeated
  regularisations, missing punches, present-without-punch, leave exhausted, new joinees, exits …
- **Report**: board dashboard, payroll leave sheet (same columns as the old manual MIS + ledger), per-employee
  drill-down (calendar, punches with location names), Excel workbook with live formulas, single-file HTML board pack.
- **Correct** anything manually (reason mandatory, audit-logged), **finalise** the month for payroll.
- **Reconcile** against the manual MIS during the parallel run (Oct-2026: 91 of 97 employees identical; the rest are
  explained inconsistencies in the manual sheet).

## Quick start (development)
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m app.cli create-admin
.venv\Scripts\python run_server.py --dev          # http://localhost:8443
.venv\Scripts\python -m pytest -q                 # tests (e2e needs: python -m playwright install chromium)
```
Production install on Windows Server: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Documentation
| | |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Orientation + rules for maintaining this project |
| [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) | Requirements, decisions, open questions |
| [docs/DATA_ANALYSIS.md](docs/DATA_ANALYSIS.md) | Every export & the manual working, findings |
| [docs/BUSINESS_RULES.md](docs/BUSINESS_RULES.md) | Calculation specification |
| [docs/METRICS.md](docs/METRICS.md) | KPIs, charts, flags |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Code map, database, security design |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Monthly workflow |
| [docs/TESTING.md](docs/TESTING.md) | Test suite and latest results |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | History |

> Client data (`reference/`, `data/`) is never committed. Place-name gazetteer: GeoNames (CC-BY 4.0).
> Chart.js (MIT) is vendored in `app/static/vendor`.
