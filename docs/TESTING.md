# Testing

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m playwright install chromium        # once, for tests/e2e
.venv\Scripts\python -m pytest -q                           # everything (≈ 1 min)
.venv\Scripts\python -m pytest -q --ignore=tests/e2e        # without browser
```
Tests marked as needing `reference/` (real client data, git-ignored) skip automatically elsewhere; synthetic
greytHR-format files from `tests/fixtures/make_fixtures.py` cover the same paths.

| File | What it proves |
|---|---|
| `test_engine.py` (37) | deduction blocks incl. boundaries (0.35 h → ½, 4.0 → ½, 4.0001 → 1), shift parsing, weekly-off patterns (Oct-2026 = 23/22/27 days), off-day hours excluded, comp-off ≥ 5 h, PL/A/LOP/COF buckets, half days, hours/day & month overrides, day overrides, calendar overrides, WFH methods, late/very-late, persistent late basis, missing punch, regularisation, auto attendance, present-without-punch, joiners/leavers, no-attendance |
| `test_parsers.py` (6) | value parsing, detection of every export & template, bad files, coordinates in columns or text, offline geocoder (Andheri, Connaught Place), real files detected incl. wrong `<dimension>` (DOJ) and 1M-row used range (MIS) |
| `test_reference_reconciliation.py` (5) | **engine vs manual October MIS: 91/97 identical on all 12 columns; the 6 known differences asserted field by field; deductions identical (+ HS207 finding); Summary total hours reconcile for all; WFH-over-limit list; behaviour findings** |
| `test_web.py` (14) | login required everywhere, lockout, forced TOTP enrolment, TOTP challenge, TOTP replay rejected, recovery codes single-use, CSRF enforced, email OTP enrol + sign-in, passkey options, roles (viewer 403s), user creation → temp password → forced change, imports + calculations on synthetic data, every page renders, overrides + ledger + audit, settings/calendars/geofence/master edits recompute, finalise lock (409) + re-open, Excel (sheets, live formulas, formula logic = app), HTML pack self-contained, templates round-trip, bad upload reported |
| `test_web_reference.py` (1) | real files through HTTP → HR settings via UI → reconciliation page shows **91 of 97** → dashboard/flags/drill-downs → Excel deductions → board pack |
| `e2e/test_browser.py` (1) | Chromium: sign-in + TOTP enrolment, upload, dashboard charts render, sheet filtering, drill-down, day correction form, Excel & board-pack downloads, board pack offline (file://) drill-down modal, **passkey registration and passkey sign-in** (virtual authenticator), no JS errors on any page |

## Latest result — 2026-11-17
`66 passed` (Windows 11, Python 3.13.15, Chromium via Playwright 1.65), real reference data present.
Screenshots of the e2e run: `test-results/*.png` (git-ignored).

## Bugs found by testing (fixed)
- openpyxl read-only mode trusted greytHR's wrong `<dimension>` → DOJ report read as 1 column → `reset_dimensions()`.
- Jinja autoescaped embedded JSON → dashboard charts did not render → `Markup` with `</` neutralised.
- Board pack: search box `change` on blur re-rendered rows mid-click → click lost → listen to `input` only.
- **Found in manual use (1.0.1):** first 2FA code → HTTP 500 (`database is locked` because commits happened after
  the response; then a non-Latin-1 flash cookie). Fixed + regression tests; the in-process TestClient could not
  reproduce the timing, so the live server is now also checked with a real browser.
- Fixture/time arithmetic and test expectations corrected (auto-attendance semantics).
