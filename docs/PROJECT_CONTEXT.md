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
- (2026-11-17, v1.4) Location names must come **automatically** from coordinates — users won't type locations; use a
  free package/data set. The importer must be **universal**: interchanged columns or a different report must still be
  understood. **More employee KPIs.** The HTML board pack must be "much, much better" with all the interactions,
  sorts and filters.

## Scope delivered (v1.0.0)
See CHANGELOG. In short: importers for all 4 greytHR exports + manual MIS + templates; policy engine; leave & comp-off
ledgers; 25 flag types; board dashboard; payroll leave sheet; employee drill-down with calendar & punches; day/month
overrides with mandatory reasons; period finalise/lock; reconciliation vs manual MIS and vs greytHR summary;
Excel workbook (live formulas) + standalone HTML board pack (removed in v1.6.0 — see below); offline location naming; users/roles;
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
| Offline gazetteer for place names (admin-named places removed in v1.6.2); online OSM off by default | "Data stays on the server" |
| 2FA mandatory by default | Requirement; admin can relax in Settings |
| Place names from a bundled GeoNames India gazetteer (566k places, offline) rather than an online API | Automatic, free (CC-BY), and no coordinates leave the server |
| Importer matches fields by meaning + checks values; unsure → user maps once, layout remembered | Never import a misread file silently; never ask twice for the same layout |
| Attention score is a transparent weighted sum with listed reasons; absence as plain days (Bradford removed at the owner's request, 2026-11-18) | Board needs "who to talk to and why", explainable in one sentence (METRICS "Insights") |

## Open questions for HR / partners (assumptions in force until answered)
1. **HS207**: should auto-marked hours on holidays count? (app says no → 0.5 day deduction for Oct.)
2. Leave credit: annual 15 days on 1-Apr pro-rata (assumed) or monthly 1.25? Policy says "other criteria remain the same".
3. Should leavers in their exit month be exempt from shortfall deduction? (Currently only via group/override.)
4. Opening leave & comp-off balances at go-live — need a one-time upload (template provided).
5. LOP: leave column or absent column? (default leave).
6. ~~Which greytHR report carries sign-in coordinates?~~ Answered 2026-11-18: the newer *Employee Swipe Details*
   export (Longitude/Latitude columns, phone, remarks) — supported from v1.5.0.
7. ~~Office/client geofences~~ Closed 2026-11-18: the owner removed named places — names come from the built-in
   map only.
8. Is "late" for the board > 1 hour (default) or another threshold?

## Decisions 2026-11-18
- **Board pack removed** (owner): "the app does it in a much better way". Board members use the app (viewer role).
- **Bradford factor removed** (owner); absence shown as plain unplanned days and spells.
- Punch locations must also be available in Excel → punches & locations workbook + Places column in Daily Detail.
- **WFH on a person's own weekly off / holiday is not WFH** (owner, v1.6.4); weekly offs and holidays are per
  person; **targeted holidays** for chosen locations / groups / employees.
- **Named places removed** (owner, v1.6.2): "we are using locations from the package" — no Settings Locations tab; the
  office check that depended on named offices went with it; shared-phone check kept.
