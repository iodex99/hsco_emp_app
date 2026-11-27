// HSCO UI helpers (no dependencies besides Chart.js). Keep in sync with docs/ARCHITECTURE.md "Front end".
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const csrf = () => ($('meta[name="csrf"]') || {}).content || "";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const H = (window.HSCO = window.HSCO || {});
  H.esc = esc;

  // ---------- top bar ----------
  const tb = $("#topbar");
  if (tb) addEventListener("scroll", () => tb.classList.toggle("scrolled", scrollY > 4), { passive: true });
  const umb = $("#um-btn"), umm = $("#um-menu");
  if (umb) {
    umb.addEventListener("click", (e) => { e.stopPropagation(); umm.classList.toggle("open"); });
    document.addEventListener("click", () => umm.classList.remove("open"));
  }
  const ps = $("#period-switch");
  if (ps) ps.addEventListener("change", () => {
    const k = ps.value, p = location.pathname;
    if (/^\/periods\/\d{4}-\d{2}/.test(p)) location.href = p.replace(/^\/periods\/\d{4}-\d{2}/, "/periods/" + k);
    else { const u = new URL(location.href); u.searchParams.set("period", k); if (!["/", "/flags", "/reconciliation"].includes(p)) u.pathname = "/"; location.href = u; }
  });

  // ---------- toasts ----------
  H.toast = (msg) => {
    const t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status");
    t.innerHTML = `<svg class="ic"><use href="${window.HSCO_ICONS || "/static/icons.svg"}#i-circle-check"></use></svg><span>${esc(msg)}</span><button type="button" aria-label="Dismiss">✕</button>`;
    t.querySelector("button").onclick = () => t.remove();
    $("#toasts").appendChild(t); setTimeout(() => t.remove(), 6000);
  };
  $$("#toasts .toast").forEach((t) => setTimeout(() => t.remove(), 7000));
  try { const m = sessionStorage.getItem("hsco-toast"); if (m) { sessionStorage.removeItem("hsco-toast"); setTimeout(() => H.toast(m), 50); } } catch (e) {}

  // ---------- global employee search ----------
  const qi = $("#q-global"), qr = $("#q-results");
  let people = null, act = -1;
  async function loadPeople() {
    if (people) return people;
    const r = await fetch("/api/employees", { credentials: "same-origin" });
    people = r.ok ? await r.json() : [];
    return people;
  }
  function renderResults(list) {
    act = list.length ? 0 : -1;
    qr.innerHTML = list.length ? list.map((e, i) => `<a href="${esc(e.href)}" class="${i === 0 ? "act" : ""}"><span class="person"><span class="av">${esc(e.initials)}</span><span><b>${esc(e.name)}</b><span>${esc(e.emp_no)} · ${esc(e.group || "—")}</span></span></span><span class="meta">${e.flags ? `<span class="badge b-warn">${e.flags} flag${e.flags > 1 ? "s" : ""}</span>` : ""}</span></a>`).join("")
      : `<div class="muted small" style="padding:10px">No match</div>`;
    qr.classList.add("open");
  }
  if (qi) {
    qi.addEventListener("focus", loadPeople);
    qi.addEventListener("input", async () => {
      const q = qi.value.trim().toLowerCase();
      if (!q) { qr.classList.remove("open"); return; }
      const all = await loadPeople();
      renderResults(all.filter((e) => (e.name + " " + e.emp_no + " " + (e.group || "")).toLowerCase().includes(q))
        .sort((x, y) => {                                   // employee-number prefix first (in number order), then name
          const px = x.emp_no.toLowerCase().startsWith(q), py = y.emp_no.toLowerCase().startsWith(q);
          if (px !== py) return px ? -1 : 1;
          if (px) return x.emp_no.localeCompare(y.emp_no, undefined, { numeric: true });
          const nx = x.name.toLowerCase().startsWith(q), ny = y.name.toLowerCase().startsWith(q);
          return nx === ny ? x.name.localeCompare(y.name) : (nx ? -1 : 1);
        }).slice(0, 8));
    });
    qi.addEventListener("keydown", (e) => {
      const items = $$("a", qr);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault(); if (!items.length) return;
        act = (act + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items.forEach((a, i) => a.classList.toggle("act", i === act));
      } else if (e.key === "Enter" && items[act]) { e.preventDefault(); location.href = items[act].href; }
      else if (e.key === "Escape") { qr.classList.remove("open"); qi.blur(); }
    });
    document.addEventListener("click", (e) => { if (!e.target.closest(".search")) qr.classList.remove("open"); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); qi.focus(); }
    });
  }

  // ---------- tabs (buttons with data-tab inside [data-tabs]; panes [data-pane]) ----------
  function showTab(group, name) {
    $$("[data-tab]", group).forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
    const scope = group.parentElement;
    $$(":scope > [data-pane], :scope [data-pane]", scope).forEach((p) => p.classList.toggle("on", p.dataset.pane === name));
    if (group.id) try { history.replaceState(null, "", "#" + name); } catch (e) {}
  }
  $$("[data-tabs]").forEach((g) => {
    $$("[data-tab]", g).forEach((b) => b.addEventListener("click", () => showTab(g, b.dataset.tab)));
    const h = location.hash.slice(1);
    if (h && $(`[data-tab="${h}"]`, g)) showTab(g, h);
  });
  $$("[data-tab-link]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault(); const name = a.dataset.tabLink; const g = $(`[data-tabs] [data-tab="${name}"]`);
    if (g) { showTab(g.closest("[data-tabs]"), name); g.scrollIntoView({ behavior: "smooth", block: "start" }); }
  }));

  // ---------- tables: search / filters / sort / group / columns / views / CSV live in table.js ----------
  // whole-row links
  document.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-href]");
    if (tr && !e.target.closest("a,button,input,select,form,label")) location.href = tr.dataset.href;
  });

  // ---------- drawer ----------
  H.openDrawer = (id) => { const d = $("#" + id); d.classList.add("open"); const f = $("input:not([type=hidden]),select,textarea", d); if (f) setTimeout(() => f.focus(), 120); };
  H.closeDrawer = () => $$(".drawer.open").forEach((d) => d.classList.remove("open"));
  $$(".scrim").forEach((s) => s.addEventListener("click", H.closeDrawer));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") H.closeDrawer(); });

  // ---------- confirm & misc ----------
  $$("form[data-confirm]").forEach((f) => f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));
  $$("select[data-nav]").forEach((s) => s.addEventListener("change", () => { if (s.value) location.href = s.value; }));
  // prevent double submit
  $$("form").forEach((f) => f.addEventListener("submit", () => { if (f.method.toLowerCase() === "post") setTimeout(() => $$("button[type=submit],button:not([type])", f).forEach((b) => (b.disabled = true)), 0); }));

  // ---------- JSON POST ----------
  H.post = async function (url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() }, body: JSON.stringify(body || {}), credentials: "same-origin" });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || j.detail || "HTTP " + r.status);
    return j;
  };

  // ---------- self-update (admin) ----------
  const fmtMB = (b) => (b / 1048576).toFixed(1) + " MB";
  const ORDER = ["downloading", "verifying", "staging", "restarting", "done"];
  function setStep(phase, bad) {
    const i = ORDER.indexOf(phase);
    $$("#upd-modal .ustep").forEach((el) => {
      const j = ORDER.indexOf(el.dataset.step);
      el.classList.toggle("ok", j < i || phase === "done");
      el.classList.toggle("on", j === i && phase !== "done" && !bad);
      el.classList.toggle("bad", j === i && !!bad);
    });
  }
  function upErr(msg) {
    $$("#upd-modal .ustep.on").forEach((s) => { s.classList.toggle("on", false); s.classList.toggle("bad", true); });
    const e = $("#um-err"); e.textContent = msg; e.classList.toggle("hidden", false);
    $("#um-bar").classList.add("err"); $("#um-close").disabled = false;
    $("#um-actions").classList.toggle("hidden", false); $("#um-go").textContent = "Try again";
  }
  H.closeUpdate = () => { const m = $("#upd-modal"); if (m && !$("#um-close").disabled) m.classList.toggle("open", false); };
  H.updateDialog = (start) => {
    const u = window.HSCO_UPD; const m = $("#upd-modal"); if (!u || !m || !u.latest) return;
    const L = u.latest;
    $("#um-title").textContent = `Update to ${L.version ? "v" + L.version : "build " + L.build}`;
    $("#um-sub").textContent = `You have v${u.current.version}${u.current.build ? " (build " + u.current.build + ")" : ""} · new build ${L.build}` +
      (L.published_at ? ` · released ${new Date(L.published_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}` : "") + (L.size ? ` · ${fmtMB(L.size)}` : "");
    $("#um-notes").textContent = L.notes || "No release notes.";
    $("#um-go").onclick = () => H.startUpdate();
    m.classList.add("open");
    if (start) H.startUpdate();
  };
  H.startUpdate = async () => {
    const u = window.HSCO_UPD, target = u.latest.build;
    $("#um-err").classList.toggle("hidden", true); $("#um-bar").classList.toggle("err", false); $("#um-bar").classList.toggle("done", false);
    $("#um-progress").classList.toggle("hidden", false); $("#um-actions").classList.toggle("hidden", true);
    $("#um-notes-wrap").classList.toggle("hidden", true); $("#um-help").classList.toggle("hidden", true);
    $("#um-close").disabled = true;
    try { await H.post("/api/update/install"); } catch (e) { upErr(e.message); return; }
    const bar = $("#um-bar > i"), msg = $("#um-msg"), pct = $("#um-pct");
    const t0 = Date.now(); let wentDown = false;
    const waitBack = async () => {
      if (Date.now() - t0 > 240000) return upErr("The server did not come back. Check the service on the server, then reload.");
      try {
        const h = await (await fetch("/healthz", { cache: "no-store" })).json();
        if (h.build === target) {
          setStep("done"); bar.style.width = "100%"; $("#um-bar").classList.add("done");
          msg.textContent = `Updated to v${h.version} (build ${h.build})`; pct.textContent = "100%";
          return setTimeout(() => location.reload(), 1500);
        }
        if (wentDown) {
          const st = await (await fetch("/api/update/status", { credentials: "same-origin" })).json();
          const lr = st.last_result;
          if (lr && lr.to_build === target && (lr.status === "rolled_back" || lr.status === "failed")) return upErr(lr.error || "The update failed and was rolled back.");
        }
      } catch (e) { wentDown = true; }
      setTimeout(waitBack, 1000);
    };
    const poll = async () => {
      let st;
      try { st = await (await fetch("/api/update/status", { credentials: "same-origin" })).json(); } catch (e) { return setTimeout(poll, 600); }
      setStep(st.phase === "staged" ? "restarting" : st.phase, st.phase === "error");
      if (st.phase === "downloading") {
        bar.style.width = (st.percent * 0.85) + "%";
        msg.textContent = st.total ? `Downloading… ${fmtMB(st.bytes)} of ${fmtMB(st.total)}` : `Downloading… ${fmtMB(st.bytes)}`;
        pct.textContent = Math.round(st.percent) + "%";
        return setTimeout(poll, 300);
      }
      if (st.phase === "verifying" || st.phase === "staging") { bar.style.width = (st.phase === "verifying" ? 88 : 92) + "%"; msg.textContent = st.message; pct.textContent = ""; return setTimeout(poll, 300); }
      if (st.phase === "error") return upErr(st.error || "Update failed");
      if (st.phase === "staged") { bar.style.width = "95%"; msg.textContent = st.message; $("#um-close").disabled = false; return; }
      if (st.phase === "restarting") { bar.style.width = "95%"; msg.textContent = "Backing up, installing and restarting…"; pct.textContent = ""; return waitBack(); }
      setTimeout(poll, 500);
    };
    poll();
  };
  (function updatedToast() {
    const u = window.HSCO_UPD; if (!u || !u.last_result) return;
    const lr = u.last_result, key = "upd-seen-" + lr.at;
    try { if (localStorage.getItem(key)) return; localStorage.setItem(key, "1"); } catch (e) { return; }
    if (lr.status === "applied" && lr.to_build === u.current.build) H.toast(`Updated to v${lr.version || u.current.version} (build ${lr.to_build})`);
    else if (lr.status === "rolled_back" || lr.status === "failed") H.toast(`Update to build ${lr.to_build} did not install and was rolled back: ${lr.error || ""}`);
  })();

  // ---------- downloads: feedback while the workbook is being built (a few seconds) ----------
  document.addEventListener("click", (e) => {
    const a = e.target.closest('a[href*="/exports/"][href$=".xlsx"]');
    if (!a || a.classList.contains("is-busy")) return;
    const html = a.innerHTML;
    a.classList.add("is-busy"); a.setAttribute("aria-busy", "true");
    a.innerHTML = html.replace(/(<\/svg>)?\s*[^<]*$/, (m, svg) => (svg || "") + " Preparing…");
    setTimeout(() => { a.classList.remove("is-busy"); a.removeAttribute("aria-busy"); a.innerHTML = html; }, 6000);
  });

  // ---------- ranked lists: top N + search + group filter + total (v1.13) ----------
  (function () {
    let saved = null; try { saved = localStorage.getItem("hsco.toplist.n"); } catch (e) {}
    const fmt = (v) => (Math.round(v * 100) / 100).toLocaleString("en-IN");
    document.querySelectorAll("[data-toplist]").forEach((box) => {
      const sel = box.querySelector(".tl-bar select"), q = box.querySelector(".tl-bar input[type=search]");
      const items = [...box.querySelectorAll("li[data-q]")], none = box.querySelector("li.tl-none");
      const cnt = box.querySelector("[data-tl-count]"), of = box.querySelector("[data-tl-of]"), sum = box.querySelector("[data-tl-sum]");
      const grp = box.querySelector(".tl-grp"), gLabel = grp && grp.querySelector("summary span");
      const gBoxes = grp ? [...grp.querySelectorAll("input[type=checkbox]")] : [];
      if (sel && saved !== null && sel.querySelector(`option[value="${saved}"]`)) sel.value = saved;
      function apply() {
        const n = sel ? +sel.value : 0, s = q ? q.value.trim().toLowerCase() : "";
        const gs = new Set(gBoxes.filter((x) => x.checked).map((x) => x.value));
        const all = !gs.size || gs.size === gBoxes.length;
        let shown = 0, matched = 0, total = 0;
        items.forEach((li) => {
          const inGroup = all || gs.has(li.dataset.g || "");
          const hit = inGroup && (!s || li.dataset.q.includes(s));
          if (hit) { matched++; total += parseFloat(li.dataset.v) || 0; }
          const ok = hit && (s || !n || matched <= n);
          li.hidden = !ok; li.dataset.match = hit ? "1" : "0"; if (ok) shown++;
        });
        if (none) none.hidden = shown > 0;
        if (cnt) cnt.textContent = shown;
        if (of) of.textContent = matched;
        if (sum) sum.textContent = fmt(total);
        if (gLabel) gLabel.textContent = all ? "All groups" : gs.size === 1 ? [...gs][0] || "(no group)" : gs.size + " groups";
        if (grp) grp.classList.toggle("on", !all);
      }
      if (sel) {
        sel.addEventListener("change", () => { try { localStorage.setItem("hsco.toplist.n", sel.value); } catch (e) {}
          document.querySelectorAll("[data-toplist] .tl-bar select").forEach((o) => { if (o !== sel && o.querySelector(`option[value="${sel.value}"]`)) { o.value = sel.value; o.dispatchEvent(new Event("tl:sync")); } });
          apply(); });
        sel.addEventListener("tl:sync", apply);
      }
      if (q) { q.addEventListener("input", apply); q.addEventListener("keydown", (e) => { if (e.key === "Escape") { q.value = ""; apply(); } }); }
      gBoxes.forEach((x) => x.addEventListener("change", apply));
      if (grp) {
        grp.querySelectorAll("[data-gall],[data-gnone]").forEach((b) => b.addEventListener("click", (e) => {
          e.preventDefault(); gBoxes.forEach((x) => (x.checked = b.hasAttribute("data-gall"))); apply(); }));
        document.addEventListener("click", (e) => { if (grp.open && !grp.contains(e.target)) grp.open = false; });
      }
      apply();
    });
  })();

  // ---------- "i" buttons: how a figure is calculated (hover, focus or tap) ----------
  (function () {
    let tip = null, owner = null;
    function hide() { if (tip) tip.remove(); tip = null; owner = null; }
    function show(el) {
      if (owner === el) return; hide();
      tip = document.createElement("div"); tip.className = "tip-pop"; tip.setAttribute("role", "tooltip");
      tip.textContent = el.dataset.tip; document.body.appendChild(tip); owner = el;
      const r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
      let top = r.bottom + 8, left = r.left + r.width / 2 - w / 2;
      if (top + h > innerHeight - 8) top = r.top - h - 8;
      left = Math.max(8, Math.min(left, document.documentElement.clientWidth - w - 8));
      tip.style.top = (scrollY + top) + "px"; tip.style.left = (scrollX + left) + "px";
    }
    document.addEventListener("mouseover", (e) => { const t = e.target.closest && e.target.closest(".info-i[data-tip]"); if (t) show(t); });
    document.addEventListener("mouseout", (e) => { const t = e.target.closest && e.target.closest(".info-i[data-tip]"); if (t && !t.contains(e.relatedTarget)) hide(); });
    document.addEventListener("focusin", (e) => { const t = e.target.closest && e.target.closest(".info-i[data-tip]"); if (t) show(t); });
    document.addEventListener("focusout", (e) => { if (e.target.closest && e.target.closest(".info-i")) hide(); });
    // inside links / tabs / sortable headers: the "i" explains, it never navigates
    document.addEventListener("click", (e) => { const t = e.target.closest && e.target.closest(".info-i"); if (t) { e.preventDefault(); e.stopPropagation(); owner === t ? hide() : show(t); } }, true);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
    addEventListener("scroll", hide, { passive: true });
  })();

  // ---------- who is behind a number: a panel listing those employees (charts, attention list…) ----------
  // H.drill({title, sub, cols:[{label, dec, unit, sum}], rows:[[emp_no, v1, v2…]], who:{emp:[name, group]},
  //          href:(emp)=>url, link:{href, text}})  — searchable, filterable by group, sortable, CSV, totals.
  H.drill = (o) => {
    let d = $("#drill");
    if (!d) {
      d = document.createElement("aside"); d.className = "drawer wide"; d.id = "drill"; d.setAttribute("aria-label", "Employees");
      d.innerHTML = `<div class="dh"><div style="min-width:0"><h2 id="drill-h"></h2><div class="small muted" id="drill-s"></div></div><span class="spacer"></span>
        <button class="btn ghost icon" type="button" aria-label="Close" data-close>✕</button></div><div class="db" id="drill-b"></div>`;
      const s = document.createElement("div"); s.className = "scrim";
      document.body.append(d, s); s.addEventListener("click", H.closeDrawer); $("[data-close]", d).onclick = H.closeDrawer;
    }
    const who = o.who || {}, cols = o.cols || [];
    const num = (v, c) => v === null || v === undefined || v === "" ? "" : (typeof v === "number" ? (+v.toFixed(c.dec ?? 2)).toLocaleString("en-IN") + (c.unit || "") : esc(v));
    const rows = (o.rows || []).map((r) => { const w = who[r[0]] || [r[0], ""]; return { emp: r[0], name: w[0], group: w[1] || "", vals: r.slice(1) }; });
    $("#drill-h", d).textContent = o.title || "";
    $("#drill-s", d).textContent = (o.sub ? o.sub + " · " : "") + rows.length + (rows.length === 1 ? " person" : " people");
    const href = o.href || (() => "");
    const head = `<tr><th data-key="emp" data-type="text">Employee</th><th data-key="g" data-type="enum">Group</th>${cols.map((c, i) => `<th data-key="v${i}" data-type="${c.text ? "text" : "num"}" class="${c.text ? "" : "num"}">${esc(c.label)}</th>`).join("")}</tr>`;
    const body = rows.map((r) => `<tr${href(r.emp) ? ` data-href="${esc(href(r.emp))}"` : ""}><td data-v="${esc(r.name)}" data-csv="${esc(r.name)} (${esc(r.emp)})"><div class="person"><span class="av">${esc((r.name || "?").split(/\s+/).filter(Boolean).map((x, i, a) => i === 0 || i === a.length - 1 ? x[0] : "").join("").toUpperCase())}</span><div style="min-width:0">${href(r.emp) ? `<a href="${esc(href(r.emp))}"><b>${esc(r.name)}</b></a>` : `<b>${esc(r.name)}</b>`}<span>${esc(r.emp)}</span></div></div></td><td class="small">${esc(r.group)}</td>${cols.map((c, i) => c.text ? `<td class="small">${esc(r.vals[i] ?? "")}</td>` : `<td class="num" data-v="${r.vals[i] ?? ""}">${num(r.vals[i], c)}</td>`).join("")}</tr>`).join("");
    const foot = `<tr class="tfoot"><td>Total · <span data-count="#drill-t">${rows.length}</span> people</td><td></td>${cols.map((c) => c.sum === false || c.text ? "<td></td>" : `<td class="num" data-sum${c.unit ? ` data-unit="${esc(c.unit)}"` : ""}${c.dec !== undefined ? ` data-dec="${c.dec}"` : ""}></td>`).join("")}</tr>`;
    $("#drill-b", d).innerHTML = (o.note ? `<p class="small muted" style="margin:0 0 10px">${esc(o.note)}</p>` : "") +
      `<div data-table-for="#drill-t"></div><div class="tw" style="max-height:none"><table id="drill-t" class="compact" data-csv="${esc(o.csv || "employees")}"><thead>${head}</thead><tbody>${body || `<tr><td colspan="${cols.length + 2}" class="muted">Nobody.</td></tr>`}</tbody><tfoot>${foot}</tfoot></table></div>` +
      (o.link ? `<p style="margin-top:12px"><a class="btn sec sm" href="${esc(o.link.href)}">${esc(o.link.text)}</a></p>` : "");
    if (H.Table) { const T = new H.Table($("#drill-t", d)); if (H.tables) H.tables["drill-t"] = T; }
    H.openDrawer("drill");
  };
  // group filter shortcut for any data-table: ?group=… is not used; the Filter → Group button of the toolbar does it

  // ---------- charts ----------
  H.colors = { brand: "#2e5bdb", brand2: "#7b9cff", ok: "#17b26a", warn: "#f79009", bad: "#f04438", violet: "#7a5af8", teal: "#15b79e", grey: "#98a2b3" };
  H.palette = ["#2e5bdb", "#15b79e", "#7b9cff", "#f79009", "#7a5af8", "#f04438", "#98a2b3", "#ee46bc", "#0ba5ec", "#669f2a"];
  if (window.Chart) {
    const d = Chart.defaults;
    d.font.family = '"Inter","Segoe UI",system-ui,sans-serif'; d.font.size = 12; d.color = "#667085"; d.maintainAspectRatio = false;
    d.plugins.legend.labels.boxWidth = 10; d.plugins.legend.labels.boxHeight = 10; d.plugins.legend.labels.usePointStyle = true;
    d.plugins.legend.position = "bottom";
    d.plugins.tooltip.backgroundColor = "#101828"; d.plugins.tooltip.padding = 10; d.plugins.tooltip.cornerRadius = 8;
    d.elements.bar.borderRadius = 5; d.elements.bar.borderSkipped = false; d.datasets.bar.maxBarThickness = 34;
    d.elements.line.borderWidth = 2.5; d.elements.point.radius = 3;
    d.scale.grid.color = "#eef0f3"; d.scale.border.display = false;
  }
  const WDN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  H.dayLabel = (iso) => { const t = new Date(iso + "T00:00:00"); return `${WDN[t.getDay()]} ${t.getDate()} ${MON[t.getMonth()]} ${t.getFullYear()}`; };
  H.charts = {};
  H.twoLineX = {
    id: "twoLineX",
    afterDraw(chart, _args, opts) {
      const sc = chart.scales.x, lines = opts && opts.lines; if (!sc || !lines) return;
      const ctx = chart.ctx, fam = (Chart.defaults.font && Chart.defaults.font.family) || "sans-serif";
      ctx.save(); ctx.textAlign = "center"; ctx.textBaseline = "top";
      sc.ticks.forEach((t, i) => {
        const l = lines[t.value !== undefined ? t.value : i]; if (!l) return;
        const x = sc.getPixelForTick(i), y = sc.top + 6;
        ctx.font = `600 11px ${fam}`; ctx.fillStyle = opts.top; ctx.fillText(l[0], x, y);
        ctx.font = `500 10px ${fam}`; ctx.fillStyle = opts.bottom; ctx.fillText(l[1], x, y + 13);
      });
      ctx.restore();
    },
  };
  // Extra options (v1.13): cfg.dates = ISO dates of the labels (tooltip title "Thu 9 Oct 2026"); cfg.total = stacked
  // tooltip footer with the total; cfg.share = single series: "n (x % of all)"; cfg.drill = (index, datasetIndex) =>
  // opens the people behind that bar / slice (pointer cursor, "Click a bar" hint is the template's job).
  H.chart = (id, cfg) => {
    const el = document.getElementById(id); if (!el || !window.Chart) return;
    cfg.options = cfg.options || {}; cfg.options.scales = cfg.options.scales || {};
    const o = cfg.options; o.plugins = o.plugins || {}; o.plugins.tooltip = o.plugins.tooltip || {};
    const cb = (o.plugins.tooltip.callbacks = o.plugins.tooltip.callbacks || {});
    if (cfg.type === "bar" || cfg.type === "line") {
      const ix = o.indexAxis === "y" ? "y" : "x";
      o.scales[ix] = Object.assign({ grid: { display: false } }, o.scales[ix] || {});
    }
    if (cfg.dates) { const ds = cfg.dates; cb.title = cb.title || ((items) => items.length ? H.dayLabel(ds[items[0].dataIndex]) : ""); }
    if (cfg.total || (cfg.data.datasets || []).length > 1) o.interaction = Object.assign({ mode: "index", intersect: false }, o.interaction || {});
    if (cfg.total) cb.footer = cb.footer || ((items) => { const t = items.filter((i) => (i.dataset.stack || i.dataset.type !== "line")).reduce((a, i) => a + (+i.raw || 0), 0); return "Total: " + (+t.toFixed(1)).toLocaleString("en-IN"); });
    if (cfg.share) cb.label = cb.label || ((c) => { const all = c.dataset.data.reduce((a, b) => a + (+b || 0), 0); const v = +c.raw || 0; return ` ${c.dataset.label ? c.dataset.label + ": " : ""}${v.toLocaleString("en-IN")}${all ? ` (${(100 * v / all).toFixed(1)}% of ${all.toLocaleString("en-IN")})` : ""}`; });
    if (cfg.drill) {
      const fn = cfg.drill;
      o.onClick = (evt, els, chart) => {
        // the bar / segment / point under the pointer (not the whole "index" column the tooltip shows)
        let hit = chart.getElementsAtEventForMode(evt, "nearest", { intersect: true }, false);
        if (!hit.length) hit = chart.getElementsAtEventForMode(evt, "index", { intersect: false }, false);
        if (hit.length) fn(hit[0].index, hit[0].datasetIndex, chart);
      };
      o.onHover = (evt, els) => { evt.native && (evt.native.target.style.cursor = els && els.length ? "pointer" : "default"); };
      if (!cb.afterFooter) cb.afterFooter = () => "Click to see who";
      el.closest(".chartbox") && el.closest(".chartbox").classList.add("clickable");
    }
    // two-line x labels drawn by hand: the date in red, the weekday initial in grey below (owner, v1.15)
    if (cfg.xLines) {
      const xs = (o.scales.x = o.scales.x || {});
      xs.ticks = Object.assign({}, xs.ticks, { callback: () => ["", ""], autoSkip: false, maxRotation: 0 });
      o.plugins.twoLineX = { lines: cfg.xLines, top: "#d92d20", bottom: "#98a2b3" };
      cfg.plugins = (cfg.plugins || []).concat([H.twoLineX]);
    }
    delete cfg.dates; delete cfg.total; delete cfg.share; delete cfg.drill; delete cfg.xLines;
    const ch = new Chart(el, cfg); H.charts[id] = ch; return ch;
  };

  // ---------- PDF: this page, or the full report of the month — built in the browser, nothing leaves the server ----------
  // menu (ui.pdf_menu): [data-pdf="title"] = this page, every tab; [data-pdf-full="2026-10"] = all report pages
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-pdf],[data-pdf-full]"); if (!b) return;
    e.preventDefault();
    const menu = b.closest(".pdf-menu"), sum = menu ? menu.querySelector("summary") : b;
    if (menu) menu.open = false;
    if (sum.classList.contains("is-busy")) return;
    const html = sum.innerHTML; sum.classList.add("is-busy"); sum.innerHTML = H.esc("Preparing PDF…");
    const done = () => { sum.classList.remove("is-busy"); sum.innerHTML = html; };
    const load = (src) => new Promise((ok, bad) => { if (document.querySelector(`script[src="${src}"]`)) return ok(); const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = () => bad(new Error("could not load " + src)); document.head.appendChild(s); });
    const v = window.HSCO_ASSET_V || "";
    load("/static/vendor/jspdf.umd.min.js").then(() => load("/static/vendor/jspdf.plugin.autotable.min.js")).then(() => load("/static/pdf.js?v=" + v))
      .then(() => (b.dataset.pdfFull ? H.pdfFull(b.dataset.pdfFull, b.dataset.label) : H.pdf(b.dataset.pdf || document.title)))
      .catch((err) => { console.error(err); H.toast("The PDF could not be made: " + err.message); }).finally(done);
  });
  document.addEventListener("click", (e) => { document.querySelectorAll(".pdf-menu[open]").forEach((m) => { if (!m.contains(e.target)) m.open = false; }); });
})();
