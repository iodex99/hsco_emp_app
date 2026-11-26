// HSCO table toolkit — search, typed filters, multi-sort, group-by, columns, saved views, CSV export.
// Usage: <table id="x" data-table> with <th data-key data-type="text|num|date|enum|none" [data-label]>.
// Cells may carry data-v (raw value: number / ISO date / sort key). Rows may carry data-tags for quick chips
// ([data-chips="#x"]). Footer cells with data-sum are recomputed for the visible rows.
// Options on the table: data-toolbar="none" (header sort only), data-csv="file-name", data-default-hidden="k1,k2".
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // icon sprite location (window.HSCO_ICONS overrides it for a page that inlines the sprite)
  const icon = (n) => `<svg class="ic sm" aria-hidden="true"><use href="${window.HSCO_ICONS ?? "/static/icons.svg"}#i-${n}"></use></svg>`;
  const coll = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  const OPS = {
    text: [["contains", "contains"], ["ncontains", "does not contain"], ["eq", "is exactly"], ["empty", "is empty"], ["nempty", "is not empty"]],
    num: [["gte", "≥"], ["lte", "≤"], ["gt", ">"], ["lt", "<"], ["eq", "="], ["neq", "≠"], ["between", "between"], ["empty", "is empty"], ["nempty", "has a value"]],
    date: [["after", "on or after"], ["before", "on or before"], ["on", "on"], ["between", "between"], ["empty", "is empty"], ["nempty", "has a date"]],
    enum: [["in", "is any of"], ["nin", "is none of"]],
  };
  const NOVAL = new Set(["empty", "nempty"]);
  const pageKey = () => location.pathname.replace(/\/\d{4}-\d{2}/g, "/*").replace(/\/[A-Z]{1,4}\d{1,5}$/i, "/*");
  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };

  // -------------------------------------------------------------------- popovers
  let openPop = null;
  function closePop() { if (openPop) { openPop.remove(); openPop = null; } }
  document.addEventListener("click", (e) => { if (openPop && !e.target.closest(".tt-pop") && !e.target.closest(".tt-btn")) closePop(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });
  function pop(btn, html, onMount, wide) {
    const was = openPop && openPop._btn === btn; closePop(); if (was) return null;
    const p = document.createElement("div"); p.className = "tt-pop" + (wide ? " wide" : ""); p._btn = btn; p.innerHTML = html;
    document.body.appendChild(p);
    const r = btn.getBoundingClientRect(), w = p.offsetWidth;
    p.style.top = (window.scrollY + r.bottom + 6) + "px";
    p.style.left = Math.max(8, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - w - 8)) + "px";
    openPop = p; if (onMount) onMount(p);
    const f = $("input:not([type=checkbox]),select", p); if (f) f.focus();
    return p;
  }

  // -------------------------------------------------------------------- table model
  class Table {
    constructor(tbl) {
      this.t = tbl; this.id = tbl.id || ("t" + Math.random().toString(36).slice(2, 7)); tbl.id = this.id;
      this.body = tbl.tBodies[0]; this.foot = tbl.tFoot && tbl.tFoot.rows[0];
      // skip group headers and "nothing here" placeholder rows (a single cell spanning the table)
      this.rows = $$(":scope > tr", this.body).filter((r) => !r.classList.contains("tt-grp") && !(r.cells.length === 1 && r.cells[0].colSpan > 1));
      this.rows.forEach((r, i) => (r._i = i));
      const ths = $$("thead tr:last-child th", tbl);
      this.cols = ths.map((th, i) => ({
        i, th, key: th.dataset.key || "c" + i, label: (th.dataset.label || th.textContent).trim() || "#" + (i + 1),
        type: th.dataset.type || this.infer(i), sum: !!(this.foot && this.foot.cells[i] && this.foot.cells[i].hasAttribute("data-sum")),
      }));
      this.byKey = Object.fromEntries(this.cols.map((c) => [c.key, c]));
      this.defaultHidden = (tbl.dataset.defaultHidden || "").split(",").filter(Boolean);
      this.state = { q: "", f: [], s: [], g: "", h: store.get(`cols:${pageKey()}:${this.id}`, this.defaultHidden), tag: "" };
      this.chips = $(`[data-chips="#${this.id}"]`);
      this.loadUrl();
      this.style = document.createElement("style"); document.head.appendChild(this.style);
      this.mode = tbl.dataset.toolbar || "full";
      if (this.mode !== "none") this.buildToolbar();
      this.bindHeaders(); this.bindChips();
      this.apply(false);
    }
    infer(i) {
      const vals = this.rows.slice(0, 60).map((r) => this.raw(r, i)).filter((v) => v !== "");
      if (!vals.length) return "text";
      if (vals.every((v) => /^\d{4}-\d{2}-\d{2}/.test(v))) return "date";
      if (vals.every((v) => !isNaN(this.num(v)))) return "num";
      const d = new Set(vals); return d.size <= 12 && d.size < this.rows.length ? "enum" : "text";
    }
    raw(r, i) { const c = r.cells[i]; if (!c) return ""; return (c.dataset.v ?? c.textContent).replace(/\s+/g, " ").trim(); }
    num(s) { if (s === "" || s === "—") return NaN; const n = parseFloat(String(s).replace(/−/g, "-").replace(/[^0-9.\-]/g, "")); return n; }
    val(r, c) {
      const s = this.raw(r, c.i);
      if (c.type === "num") return this.num(s);
      if (c.type === "date") return s;   // full ISO (sorts by time too); filters/grouping use the day part
      if (c.type === "enum") { const cell = r.cells[c.i]; return (cell && (cell.dataset.f ?? cell.textContent.replace(/\s+/g, " ").trim())) || ""; }
      return s;
    }
    // ---------------------------------------------------------------- state <-> url
    loadUrl() {
      const p = new URLSearchParams(location.search).get("t_" + this.id);
      if (p) try { Object.assign(this.state, JSON.parse(p)); } catch (e) {}
      const tag = new URLSearchParams(location.search).get("f"); if (tag && this.chips) this.state.tag = tag;
    }
    saveUrl() {
      const u = new URL(location.href), s = this.state;
      const minimal = { q: s.q || undefined, f: s.f.length ? s.f : undefined, s: s.s.length ? s.s : undefined, g: s.g || undefined };
      const has = Object.values(minimal).some((v) => v !== undefined);
      if (has) u.searchParams.set("t_" + this.id, JSON.stringify(minimal)); else u.searchParams.delete("t_" + this.id);
      if (this.chips) { if (s.tag) u.searchParams.set("f", s.tag); else u.searchParams.delete("f"); }
      try { history.replaceState(null, "", u); } catch (e) {}
    }
    // ---------------------------------------------------------------- filtering
    test(r, f) {
      const c = this.byKey[f.k]; if (!c) return true;
      const v = this.val(r, c);
      const empty = v === "" || (typeof v === "number" && isNaN(v));
      if (f.op === "empty") return empty; if (f.op === "nempty") return !empty;
      if (c.type === "enum") { const set = f.v || []; return f.op === "nin" ? !set.includes(v) : set.includes(v); }
      if (c.type === "num") {
        if (empty) return false; const a = parseFloat(f.v), b = parseFloat(f.v2);
        return { gte: v >= a, lte: v <= a, gt: v > a, lt: v < a, eq: v === a, neq: v !== a, between: v >= a && v <= b }[f.op] ?? true;
      }
      if (c.type === "date") {
        if (empty) return false;
        const d = v.slice(0, 10);
        return { after: d >= f.v, before: d <= f.v, on: d === f.v, between: d >= f.v && d <= (f.v2 || "9999") }[f.op] ?? true;
      }
      const s = v.toLowerCase(), q = String(f.v || "").toLowerCase();
      return { contains: s.includes(q), ncontains: !s.includes(q), eq: s === q }[f.op] ?? true;
    }
    match(r) {
      const s = this.state;
      if (s.tag && !(" " + (r.dataset.tags || "") + " ").includes(" " + s.tag + " ")) return false;
      if (s.q) { const q = s.q.toLowerCase(); if (!(r._txt || (r._txt = r.textContent.replace(/\s+/g, " ").toLowerCase())).includes(q)) return false; }
      return s.f.every((f) => this.test(r, f));
    }
    cmp(a, b, keys) {
      for (const { k, d } of keys) {
        const c = this.byKey[k]; if (!c) continue;
        const sa = a.cells[c.i] && a.cells[c.i].dataset.s, sb = b.cells[c.i] && b.cells[c.i].dataset.s;
        if (sa !== undefined && sb !== undefined) { const r0 = coll.compare(sa, sb); if (r0) return d === "desc" ? -r0 : r0; continue; }
        const x = this.val(a, c), y = this.val(b, c); let r;
        if (c.type === "num") { const xn = isNaN(x), yn = isNaN(y); r = xn && yn ? 0 : xn ? 1 : yn ? -1 : x - y; if (xn || yn) { if (r) return r; continue; } }
        else { const xe = x === "", ye = y === ""; if (xe || ye) { r = xe && ye ? 0 : xe ? 1 : -1; if (r) return r; continue; } r = coll.compare(x, y); }
        if (r) return d === "desc" ? -r : r;
      }
      return a._i - b._i;
    }
    // ---------------------------------------------------------------- render
    apply(save = true) {
      const s = this.state;
      $$(":scope > tr.tt-grp", this.body).forEach((g) => g.remove());
      const keys = (s.g ? [{ k: s.g, d: "asc" }] : []).concat(s.s);
      const sorted = this.rows.slice().sort((a, b) => this.cmp(a, b, keys));
      const shown = [];
      this._order = sorted;                                  // the order on screen (the CSV follows it)
      sorted.forEach((r) => { const ok = this.match(r); r.style.display = ok ? "" : "none"; r._shown = ok; if (ok) shown.push(r); this.body.appendChild(r); });
      if (s.g && this.byKey[s.g]) this.renderGroups(shown);
      this.renderHidden(); this.renderSortMarks(); this.renderTotals(shown);
      if (this.mode !== "none") this.renderBar(shown.length);
      const cnt = $(`[data-count="#${this.id}"]`); if (cnt) cnt.textContent = shown.length;
      if (this.chips) $$(".chip", this.chips).forEach((x) => x.classList.toggle("on", (x.dataset.chip || "") === (s.tag || "")));
      if (save) this.saveUrl();
      this.t.dispatchEvent(new CustomEvent("tt:apply", { detail: { shown: shown.length } }));
    }
    renderGroups(shown) {
      const gc = this.byKey[this.state.g], collapsed = this._collapsed || (this._collapsed = new Set());
      let cur = null, members = [];
      const flush = () => {
        if (cur === null) return;
        const tr = document.createElement("tr"); tr.className = "tt-grp"; tr.dataset.g = cur;
        const isC = collapsed.has(cur);
        this.cols.forEach((c, i) => {
          const td = document.createElement("td");
          if (i === 0) { td.innerHTML = `<button type="button" class="tt-tog">${isC ? "▸" : "▾"}</button> <b>${esc(cur || "(blank)")}</b> <span class="muted">· ${members.length}</span>`; td.className = "sticky"; }
          else if (c.sum) { const v = members.reduce((t, r) => t + (isNaN(this.val(r, c)) ? 0 : this.val(r, c)), 0); td.className = "num"; td.textContent = Number.isInteger(v) ? v : parseFloat(v.toFixed(2)); }
          tr.appendChild(td);
        });
        members[0].before(tr);
        if (isC) members.forEach((r) => (r.style.display = "none"));
        tr.querySelector(".tt-tog").onclick = () => { isC ? collapsed.delete(tr.dataset.g) : collapsed.add(tr.dataset.g); this.apply(false); };
      };
      shown.forEach((r) => { const v = this.val(r, gc); const g = typeof v === "number" ? String(v) : gc.type === "date" ? v.slice(0, 10) : v; if (g !== cur) { flush(); cur = g; members = []; } members.push(r); });
      flush();
    }
    renderHidden() {
      const h = this.state.h.map((k) => this.byKey[k]).filter(Boolean);
      this.style.textContent = h.map((c) => `#${this.id} tr > :nth-child(${c.i + 1}){display:none}`).join("\n");
    }
    renderSortMarks() {
      this.cols.forEach((c) => { delete c.th.dataset.dir; c.th.removeAttribute("data-order"); });
      this.state.s.forEach((x, n) => { const c = this.byKey[x.k]; if (!c) return; c.th.dataset.dir = x.d; if (this.state.s.length > 1) c.th.dataset.order = n + 1; });
    }
    renderTotals(shown) {
      if (!this.foot) return;
      $$("td", this.foot).forEach((td, i) => {
        if (!td.hasAttribute("data-sum")) return;
        const c = this.cols[i]; let v = 0;
        shown.forEach((r) => { const x = c ? this.val(r, { ...c, type: "num" }) : NaN; if (!isNaN(x)) v += x; });
        const dec = td.dataset.dec !== undefined ? +td.dataset.dec : 2;
        td.textContent = (Number.isInteger(v) ? v : parseFloat(v.toFixed(dec))) + (td.dataset.unit || "");   // 90.5, not 90.50
      });
    }
    // ---------------------------------------------------------------- headers & chips
    bindHeaders() {
      this.cols.forEach((c) => {
        if (c.type === "none") return;
        c.th.classList.add("sortable"); c.th.title = "Click to sort · Shift-click to add a second sort";
        // click cycle: default direction (numbers/dates high→low, text A→Z) → reverse → off
        const first = c.type === "num" || c.type === "date" ? "desc" : "asc";
        const flip = (d) => (d === "asc" ? "desc" : "asc");
        c.th.addEventListener("click", (e) => {
          const s = this.state.s, at = s.findIndex((x) => x.k === c.key);
          if (e.shiftKey) { if (at < 0) s.push({ k: c.key, d: first }); else if (s[at].d === first) s[at].d = flip(first); else s.splice(at, 1); }
          else if (at === 0 && s.length === 1) { if (s[0].d === first) s[0].d = flip(first); else s.splice(0, 1); }
          else this.state.s = [{ k: c.key, d: first }];
          this.apply();
        });
      });
    }
    bindChips() {
      if (!this.chips) return;
      $$(".chip", this.chips).forEach((x) => x.addEventListener("click", () => { this.state.tag = x.dataset.chip || ""; this.apply(); }));
    }
    // ---------------------------------------------------------------- toolbar
    buildToolbar() {
      let host = $(`[data-table-for="#${this.id}"]`);
      if (!host) { host = document.createElement("div"); (this.t.closest(".tw") || this.t).before(host); }
      host.classList.add("tt");
      host.innerHTML = `
        <div class="tt-row">
          <label class="tt-search">${icon("search")}<input type="search" placeholder="Search…" aria-label="Search table" value="${esc(this.state.q)}"></label>
          <button type="button" class="tt-btn" data-a="filter">${icon("filter")} Filter<span class="tt-n" data-n="f"></span></button>
          <button type="button" class="tt-btn" data-a="sort">${icon("arrow-up-down")} Sort<span class="tt-n" data-n="s"></span></button>
          <button type="button" class="tt-btn" data-a="group">${icon("layers")} Group<span class="tt-n" data-n="g"></span></button>
          <button type="button" class="tt-btn" data-a="columns">${icon("columns-3")} Columns<span class="tt-n" data-n="h"></span></button>
          <button type="button" class="tt-btn" data-a="views">${icon("bookmark")} Views</button>
          <button type="button" class="tt-btn" data-a="csv" title="Download the rows shown as CSV (opens in Excel)">${icon("download")} CSV</button>
          <span class="tt-count muted small"></span>
        </div>
        <div class="tt-active"></div>`;
      this.host = host;
      const inp = $(".tt-search input", host); let tmr;
      inp.addEventListener("input", () => { clearTimeout(tmr); tmr = setTimeout(() => { this.state.q = inp.value.trim(); this.apply(); }, 120); });
      $$(".tt-btn", host).forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); this[b.dataset.a](b); }));
    }
    renderBar(n) {
      const s = this.state, h = this.host;
      $(".tt-count", h).textContent = n === this.rows.length ? `${n} rows` : `Showing ${n} of ${this.rows.length}`;
      const setN = (k, v) => { const el = $(`[data-n="${k}"]`, h); el.textContent = v ? v : ""; el.parentElement.classList.toggle("on", !!v); };
      setN("f", s.f.length); setN("s", s.s.length); setN("g", s.g ? 1 : 0); setN("h", s.h.length ? s.h.length + " hidden" : "");
      const chips = s.f.map((f, i) => {
        const c = this.byKey[f.k]; if (!c) return "";
        const op = (OPS[c.type] || OPS.text).find((o) => o[0] === f.op); let v = "";
        if (c.type === "enum") v = (f.v || []).map((x) => x || "(blank)").join(", ");
        else if (!NOVAL.has(f.op)) v = f.op === "between" ? `${f.v} – ${f.v2}` : f.v;
        return `<span class="tt-chip" data-i="${i}"><button type="button" class="tt-edit"><b>${esc(c.label)}</b> ${esc(op ? op[1] : f.op)} ${esc(v)}</button><button type="button" class="tt-x" aria-label="Remove filter">×</button></span>`;
      });
      if (s.g) chips.push(`<span class="tt-chip grp"><button type="button" class="tt-edit" data-grp>Grouped by <b>${esc((this.byKey[s.g] || {}).label)}</b></button><button type="button" class="tt-x" data-grp aria-label="Remove grouping">×</button></span>`);
      const act = $(".tt-active", h);
      act.innerHTML = chips.join("") + (chips.length || s.q || s.s.length ? `<button type="button" class="tt-clear">Clear all</button>` : "");
      $$(".tt-chip", act).forEach((ch) => {
        const i = +ch.dataset.i;
        $(".tt-x", ch).onclick = () => { if ($(".tt-x", ch).hasAttribute("data-grp")) s.g = ""; else s.f.splice(i, 1); this.apply(); };
        $(".tt-edit", ch).onclick = (e) => { e.stopPropagation(); if ($(".tt-edit", ch).hasAttribute("data-grp")) this.group($(".tt-btn[data-a=group]", h)); else this.editFilter($(".tt-edit", ch), i); };
      });
      const cl = $(".tt-clear", act); if (cl) cl.onclick = () => { s.f = []; s.s = []; s.g = ""; s.q = ""; s.tag = ""; $(".tt-search input", h).value = ""; this.apply(); };
    }
    filterable() { return this.cols.filter((c) => c.type !== "none"); }
    filter(btn) {
      const p = pop(btn, `<div class="tt-h">Filter by column</div><input class="tt-find" placeholder="Find a column…">
        <div class="tt-list">${this.filterable().map((c) => `<button type="button" data-k="${esc(c.key)}"><span>${esc(c.label)}</span><span class="tt-type">${{ num: "number", date: "date", enum: "list", text: "text" }[c.type]}</span></button>`).join("")}</div>`);
      if (!p) return;
      $(".tt-find", p).oninput = (e) => $$(".tt-list button", p).forEach((b) => (b.style.display = b.textContent.toLowerCase().includes(e.target.value.toLowerCase()) ? "" : "none"));
      $$(".tt-list button", p).forEach((b) => (b.onclick = (e) => { e.stopPropagation(); const c = this.byKey[b.dataset.k];
        this.state.f.push({ k: c.key, op: OPS[c.type][0][0], v: c.type === "enum" ? [] : "", v2: "" }); closePop(); this.editFilter(btn, this.state.f.length - 1, true); }));
    }
    editFilter(btn, idx, fresh) {
      const f = this.state.f[idx], c = this.byKey[f.k], ops = OPS[c.type] || OPS.text;
      let body = `<div class="tt-h">${esc(c.label)}</div><select class="tt-op">${ops.map((o) => `<option value="${o[0]}" ${o[0] === f.op ? "selected" : ""}>${esc(o[1])}</option>`).join("")}</select>`;
      if (c.type === "enum") {
        const counts = new Map(); this.rows.forEach((r) => { const v = this.val(r, c); counts.set(v, (counts.get(v) || 0) + 1); });
        const vals = Array.from(counts.keys()).sort(coll.compare);
        body += `${vals.length > 8 ? '<input class="tt-find" placeholder="Search values…">' : ""}<div class="tt-vals">${vals.map((v) => `<label><input type="checkbox" value="${esc(v)}" ${(f.v || []).includes(v) ? "checked" : ""}> <span>${esc(v || "(blank)")}</span><span class="muted">${counts.get(v)}</span></label>`).join("")}</div>
          <div class="tt-row2"><button type="button" class="btn sm ghost" data-all>Select all</button><button type="button" class="btn sm ghost" data-none>None</button></div>`;
      } else {
        const t = c.type === "num" ? "number" : c.type === "date" ? "date" : "text";
        body += `<div class="tt-row2 tt-vrow"><input class="tt-v" type="${t}" step="any" value="${esc(f.v ?? "")}" placeholder="Value"><span class="tt-and">and</span><input class="tt-v2" type="${t}" step="any" value="${esc(f.v2 ?? "")}"></div>`;
      }
      body += `<div class="tt-row2" style="justify-content:space-between;margin-top:10px"><button type="button" class="btn sm ghost" data-rm>Remove</button><button type="button" class="btn sm" data-done>Done</button></div>`;
      const p = pop(btn, body, null, c.type === "enum"); if (!p) return;
      const sync = () => {
        f.op = $(".tt-op", p).value;
        if (c.type === "enum") f.v = $$(".tt-vals input:checked", p).map((x) => x.value);
        else { f.v = $(".tt-v", p).value; f.v2 = $(".tt-v2", p).value; }
        const vr = $(".tt-vrow", p); if (vr) { vr.style.display = NOVAL.has(f.op) ? "none" : ""; $(".tt-and", p).style.display = $(".tt-v2", p).style.display = f.op === "between" ? "" : "none"; }
        this.apply();
      };
      $$("select,input", p).forEach((x) => x.addEventListener(x.type === "checkbox" || x.tagName === "SELECT" ? "change" : "input", (e) => { if (!x.classList.contains("tt-find")) sync(); }));
      const fnd = $(".tt-find", p); if (fnd) fnd.oninput = () => $$(".tt-vals label", p).forEach((l) => (l.style.display = l.textContent.toLowerCase().includes(fnd.value.toLowerCase()) ? "" : "none"));
      const all = $("[data-all]", p); if (all) all.onclick = () => { $$(".tt-vals label", p).forEach((l) => { if (l.style.display !== "none") $("input", l).checked = true; }); sync(); };
      const none = $("[data-none]", p); if (none) none.onclick = () => { $$(".tt-vals input", p).forEach((x) => (x.checked = false)); sync(); };
      $("[data-rm]", p).onclick = () => { this.state.f.splice(idx, 1); closePop(); this.apply(); };
      $("[data-done]", p).onclick = () => { closePop(); if (c.type !== "enum" && !NOVAL.has(f.op) && f.v === "") { this.state.f.splice(idx, 1); this.apply(); } };
      if (fresh) sync();
      const vr = $(".tt-vrow", p); if (vr) { vr.style.display = NOVAL.has(f.op) ? "none" : ""; $(".tt-and", p).style.display = $(".tt-v2", p).style.display = f.op === "between" ? "" : "none"; }
    }
    sort(btn) {
      const s = this.state, opts = this.filterable();
      const render = () => `<div class="tt-h">Sort rows</div>${s.s.map((x, i) => `<div class="tt-row2" data-i="${i}"><span class="muted small" style="width:42px">${i ? "then" : "by"}</span>
          <select class="tt-sk">${opts.map((c) => `<option value="${esc(c.key)}" ${c.key === x.k ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select>
          <select class="tt-sd"><option value="asc" ${x.d === "asc" ? "selected" : ""}>Ascending ↑</option><option value="desc" ${x.d === "desc" ? "selected" : ""}>Descending ↓</option></select>
          <button type="button" class="btn sm ghost tt-sx" aria-label="Remove">×</button></div>`).join("") || '<p class="muted small" style="margin:4px 0 8px">Original order. Add a sort level, or click a column header.</p>'}
        <div class="tt-row2" style="margin-top:8px"><button type="button" class="btn sm sec" data-add>+ Add sort level</button>${s.s.length ? '<button type="button" class="btn sm ghost" data-clr>Clear</button>' : ""}</div>`;
      const p = pop(btn, render(), null, true); if (!p) return;
      const bind = () => {
        $$("[data-i]", p).forEach((row) => { const i = +row.dataset.i;
          $(".tt-sk", row).onchange = (e) => { s.s[i].k = e.target.value; this.apply(); };
          $(".tt-sd", row).onchange = (e) => { s.s[i].d = e.target.value; this.apply(); };
          $(".tt-sx", row).onclick = (e) => { e.stopPropagation(); s.s.splice(i, 1); this.apply(); p.innerHTML = render(); bind(); }; });
        $("[data-add]", p).onclick = (e) => { e.stopPropagation(); const used = new Set(s.s.map((x) => x.k)); const c = opts.find((o) => !used.has(o.key)) || opts[0]; s.s.push({ k: c.key, d: "asc" }); this.apply(); p.innerHTML = render(); bind(); };
        const clr = $("[data-clr]", p); if (clr) clr.onclick = (e) => { e.stopPropagation(); s.s = []; this.apply(); p.innerHTML = render(); bind(); };
      };
      bind();
    }
    group(btn) {
      const cands = this.cols.filter((c) => c.type === "enum" || c.type === "text" && c.key !== this.cols[0].key || c.type === "date");
      const p = pop(btn, `<div class="tt-h">Group rows by</div><div class="tt-list">
        <button type="button" data-k="" class="${this.state.g ? "" : "on"}">No grouping</button>
        ${cands.map((c) => `<button type="button" data-k="${esc(c.key)}" class="${c.key === this.state.g ? "on" : ""}">${esc(c.label)}</button>`).join("")}</div>
        <p class="help" style="margin:8px 2px 0">Groups show a count${this.cols.some((c) => c.sum) ? " and subtotals" : ""}; click ▾ to collapse.</p>`);
      if (!p) return;
      $$("[data-k]", p).forEach((b) => (b.onclick = () => { this.state.g = b.dataset.k; this._collapsed = new Set(); closePop(); this.apply(); }));
    }
    columns(btn) {
      const s = this.state;
      const presets = this.defaultHidden.length ? `<div class="tt-row2" style="margin-bottom:8px"><button type="button" class="btn sm sec" data-p="key">Key columns</button><button type="button" class="btn sm sec" data-p="all">All columns</button></div>` : "";
      const p = pop(btn, `<div class="tt-h">Show columns</div>${presets}<div class="tt-vals">${this.cols.map((c, i) => i === 0 ? "" :
        `<label><input type="checkbox" value="${esc(c.key)}" ${s.h.includes(c.key) ? "" : "checked"}> <span>${esc(c.label)}</span></label>`).join("")}</div>`);
      if (!p) return;
      const save = () => { store.set(`cols:${pageKey()}:${this.id}`, s.h); this.apply(); };
      $$(".tt-vals input", p).forEach((x) => (x.onchange = () => { s.h = $$(".tt-vals input", p).filter((y) => !y.checked).map((y) => y.value); save(); }));
      $$("[data-p]", p).forEach((b) => (b.onclick = () => { s.h = b.dataset.p === "key" ? this.defaultHidden.slice() : []; $$(".tt-vals input", p).forEach((y) => (y.checked = !s.h.includes(y.value))); save(); }));
    }
    views(btn) {
      const key = `views:${pageKey()}:${this.id}`;
      const render = () => { const vs = store.get(key, []);
        return `<div class="tt-h">Saved views</div>${vs.length ? `<div class="tt-list">${vs.map((v, i) => `<div class="tt-view"><button type="button" data-apply="${i}">${esc(v.name)}</button><button type="button" class="tt-x" data-del="${i}" aria-label="Delete view">×</button></div>`).join("")}</div>` : '<p class="muted small" style="margin:2px 0 8px">Save the current search, filters, sort, grouping and columns to reuse them — including on other months.</p>'}
          <div class="tt-row2" style="margin-top:8px"><input class="tt-vname" placeholder="Name this view"><button type="button" class="btn sm" data-save>Save</button></div>
          <div class="tt-row2" style="margin-top:6px"><button type="button" class="btn sm ghost" data-copy>${icon("external-link")} Copy link to this view</button></div>`; };
      const p = pop(btn, render(), null, true); if (!p) return;
      const bind = () => {
        $("[data-save]", p).onclick = (e) => { e.stopPropagation(); const n = $(".tt-vname", p).value.trim(); if (!n) return $(".tt-vname", p).focus();
          const vs = store.get(key, []).filter((v) => v.name !== n); vs.push({ name: n, state: JSON.parse(JSON.stringify(this.state)) }); store.set(key, vs); p.innerHTML = render(); bind(); window.HSCO && HSCO.toast && HSCO.toast(`View "${n}" saved`); };
        $$("[data-apply]", p).forEach((b) => (b.onclick = () => { const v = store.get(key, [])[+b.dataset.apply]; this.state = Object.assign({ q: "", f: [], s: [], g: "", h: [], tag: "" }, JSON.parse(JSON.stringify(v.state)));
          if (this.host) $(".tt-search input", this.host).value = this.state.q || ""; store.set(`cols:${pageKey()}:${this.id}`, this.state.h); closePop(); this.apply(); }));
        $$("[data-del]", p).forEach((b) => (b.onclick = (e) => { e.stopPropagation(); const vs = store.get(key, []); vs.splice(+b.dataset.del, 1); store.set(key, vs); p.innerHTML = render(); bind(); }));
        $("[data-copy]", p).onclick = () => { navigator.clipboard && navigator.clipboard.writeText(location.href); closePop(); window.HSCO && HSCO.toast && HSCO.toast("Link copied"); };
      };
      bind();
    }
    // ---------------------------------------------------------------- CSV (app-wide standard, v1.8)
    // Rows exactly as shown (filter, search, sort). Employee No and Name in their own columns; numbers raw (unit in
    // the header); dates ISO (yyyy-mm-dd [hh:mm]); lists joined with "; "; blank = none; no icons, badges or initials.
    csv(btn) {
      const hidden = this.state.h.filter((k) => this.byKey[k] && this.byKey[k].type !== "none");
      if (!btn || !hidden.length) return this.exportCsv(false);
      const nv = this.cols.filter((c) => this.exportable(c) && !this.state.h.includes(c.key)).length;
      const na = this.cols.filter((c) => this.exportable(c)).length;
      const p = pop(btn, `<div class="tt-h">Download CSV</div><div class="tt-list">
        <button type="button" data-all="0"><span>Columns shown</span><span class="tt-type">${nv} columns</span></button>
        <button type="button" data-all="1"><span>All columns</span><span class="tt-type">${na} columns</span></button></div>
        <div class="small muted" style="padding:6px 10px 2px">Rows as shown — current filter, search and sort.</div>`);
      if (!p) return;
      $$("[data-all]", p).forEach((b) => (b.onclick = (e) => { e.stopPropagation(); closePop(); this.exportCsv(b.dataset.all === "1"); }));
    }
    exportable(c) { return c.type !== "none" || c.th.hasAttribute("data-export"); }
    exportCsv(all) {
      const IDRE = /^[A-Z0-9][A-Z0-9_\-\/.]{0,19}$/;
      const ALWAYS = new Set(["group", "category", "cat", "location", "loc"]);      // identity columns travel along
      const cols = this.cols.filter((c) => this.exportable(c) && (all || !this.state.h.includes(c.key) || ALWAYS.has(c.key)));
      const rows = (this._order || this.rows).filter((r) => r._shown);
      const person = (td) => {
        if (!td) return null;
        const m = /^(.*) \(([^()]+)\)$/.exec(td.dataset.csv || "");
        if (m && IDRE.test(m[2].trim())) return { name: m[1].trim(), no: m[2].trim() };
        const pe = td.querySelector(".person"); if (!pe) return null;
        const b = pe.querySelector("b"), sub = pe.querySelector("div > span");
        const no = ((sub && sub.textContent) || "").trim().split(/[\s·]+/)[0] || "";
        return { name: (b ? b.textContent : "").trim(), no: IDRE.test(no) ? no : "" };
      };
      // a column of people becomes two columns: Employee No, Name
      const isPerson = (c) => { const s = rows.slice(0, 25); return s.length && s.filter((r) => person(r.cells[c.i])).length >= 0.8 * s.length; };
      const text = (td) => {
        const tags = $$(".badge, .tag", td);
        let t = tags.length > 1 ? tags.map((x) => x.textContent.replace(/\s+/g, " ").trim()).filter(Boolean).join("; ")
                                : td.textContent.replace(/\s+/g, " ").trim();
        t = t.replace(/[✎•]/g, "").trim();
        return /^[—–-]$/.test(t) ? "" : t;
      };
      const value = (td, c) => {
        if (!td) return "";
        if (td.dataset.csv !== undefined) return td.dataset.csv;
        if (c.type === "num") {
          const raw = td.dataset.v ?? td.textContent, v = this.num(raw);
          return isNaN(v) ? text(td) : String(+v.toFixed(4));
        }
        if (c.type === "date" && td.dataset.v) return td.dataset.v.replace("T", " ").slice(0, 16);
        return text(td);
      };
      const q = (t) => {
        t = t == null ? "" : String(t);
        if (/^[=+\-@\t\r]/.test(t) && isNaN(+t)) t = "'" + t;                      // never a formula in Excel
        return /[",\n\r]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
      };
      const plan = cols.map((c) => ({ c, p: isPerson(c) }));
      const head = [];
      plan.forEach(({ c, p }) => { if (p) head.push("Employee No", c.label === "Employee" ? "Name" : c.label); else head.push(c.label); });
      const lines = [head.map(q).join(",")];
      rows.forEach((r) => {
        const out = [];
        plan.forEach(({ c, p }) => {
          if (p) { const x = person(r.cells[c.i]) || { no: "", name: value(r.cells[c.i], c) }; out.push(x.no, x.name); }
          else out.push(value(r.cells[c.i], c));
        });
        lines.push(out.map(q).join(","));
      });
      const d = new Date(), z = (n) => String(n).padStart(2, "0");
      const stamp = `${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}-${z(d.getHours())}${z(d.getMinutes())}`;
      const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
      const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
      a.download = `Attendance MIS - ${this.t.dataset.csv || this.id} - ${stamp}.csv`; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    }
  }
  window.HSCO = window.HSCO || {};
  HSCO.tables = {};
  HSCO.Table = Table;          // for tables created after page load
  $$("table[data-table]").forEach((t) => (HSCO.tables[t.id || ""] = new Table(t)));
  Object.keys(HSCO.tables).forEach((k) => { const T = HSCO.tables[k]; HSCO.tables[T.id] = T; });
})();
