# Project Context

## Client
**Hasmukh Shah & Co. LLP** — Chartered Accountants, 409/410 Dalamal Chambers, New Marine Lines, Mumbai 400020
(+ Delhi team). ~100 staff in groups: Partner, SS (support staff), BSS, ATA, GST, Offshore, Article (articled
assistants), Trainee. HR software: **greytHR** (biometric device "Astra" + greytHR mobile app sign-ins).

## Problem (owner's words, condensed — 2026-11-17)
- greytHR does not calculate leave at month end before payroll; HR does it manually in Excel every month
  (MIS Report "Reference" sheet: columns A–L exported, M–S manual working, plus pivot sheets in each export).
- Automate that calculation, and add value: board-actionable employee metrics — new joinees, persistent late punches,
  persistent regularisation requests, "many more" — in **Excel and interactive HTML**, with **drill-down per employee**.
- Weekly-off policies differ per employee ("Total Week Days" varies: some work all Saturdays, some have all off).
- Hours worked on a holiday are not counted. Statuses include comp off, holiday, present, absent (+ PL, LOP).
- WFH allowed only **2 days a month** — flag excess.
- Sign-in reports show coordinates only — show a **location name** instead.
- Allow **manual entries**.
- Installed on the firm's **Windows server**; **data stays on the server**.
- **2FA, with multiple 2FA methods configurable.**
- Maintain `.md` files for the complete project as persistent memory; update after every change.
- Commit & push to GitHub repo `iodex99/hsco_emp_app` (**public** → no client data in git).

## Scope delivered (v1.0.0)
See CHANGELOG. In short: importers for all 4 greytHR exports + manual MIS + templates; policy engine; leave & comp-off
ledgers; 25 flag types; board dashboard; payroll leave sheet; employee drill-down with calendar & punches; day/month
overrides with mandatory reasons; period finalise/lock; reconciliation vs manual MIS and vs greytHR summary;
Excel workbook (live formulas) + standalone HTML board pack; offline location naming + geofences; users/roles;
TOTP + passkeys/security keys + email codes + recovery codes; audit log; Windows installer, HTTPS, nightly backup.

## Decisions taken (and why)
| Decision | Why |
|---|---|
| Python/FastAPI + SQLite, server-rendered pages, vendored Chart.js | Single-server on-prem, no internet dependency, easy backup (one file), no DB admin needed for ~100 staff |
| Muster is the source of truth; Summary only for reconciliation | Summary's week days/shortfall are wrong (DATA_ANALYSIS §3) |
| Per-employee weekly-off pattern + holiday calendars overriding greytHR | HR's manual corrections are exactly these (DATA_ANALYSIS "Manual corrections") |
| Deduction = ceil(shortfall/4) × 0.5, no grace (setting) | Policy text + every manual remark |
| LOP shown under Leaves (G) by default | HR did so 3 of 4 times; setting available |
| Persistent late = > 60 min late on ≥ 10 days | 15-min rule flagged 66 of 94 → not actionable (BUSINESS_RULES §7) |
| WFH counted as distinct days | HR's swipes ÷ 2 gives half-days for mixed days |
| Offline gazetteer + geofences for place names; online OSM off by default | "Data stays on the server" |
| 2FA mandatory by default | Requirement; admin can relax in Settings |

## Open questions for HR / partners (assumptions in force until answered)
1. **HS207**: should auto-marked hours on holidays count? (app says no → 0.5 day deduction for Oct.)
2. Leave credit: annual 15 days on 1-Apr pro-rata (assumed) or monthly 1.25? Policy says "other criteria remain the same".
3. Should leavers in their exit month be exempt from shortfall deduction? (Currently only via group/override.)
4. Opening leave & comp-off balances at go-live — need a one-time upload (template provided).
5. LOP: leave column or absent column? (default leave).
6. Which greytHR report carries sign-in coordinates? The supplied swipe export has none — importer is ready for
   `Latitude/Longitude` columns or "lat, long" text.
7. Office/client geofences (names + coordinates) to load.
8. Is "late" for the board > 1 hour (default) or another threshold?
