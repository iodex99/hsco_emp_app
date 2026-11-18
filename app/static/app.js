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
    t.innerHTML = `<svg class="ic"><use href="/static/icons.svg#i-circle-check"></use></svg><span>${esc(msg)}</span><button type="button" aria-label="Dismiss">✕</button>`;
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
      renderResults(all.filter((e) => (e.name + " " + e.emp_no + " " + (e.group || "")).toLowerCase().includes(q)).slice(0, 8));
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

  // ---------- WebAuthn ----------
  const b64uToBuf = (s) => { s = s.replace(/-/g, "+").replace(/_/g, "/"); while (s.length % 4) s += "="; return Uint8Array.from(atob(s), (c) => c.charCodeAt(0)).buffer; };
  const bufToB64u = (b) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  H.registerPasskey = async function (label) {
    if (!window.PublicKeyCredential) throw new Error("This browser does not support security keys / passkeys (HTTPS required).");
    const o = await H.post("/security/webauthn/options");
    o.challenge = b64uToBuf(o.challenge); o.user.id = b64uToBuf(o.user.id);
    (o.excludeCredentials || []).forEach((c) => (c.id = b64uToBuf(c.id)));
    const cred = await navigator.credentials.create({ publicKey: o });
    return H.post("/security/webauthn/register", { label, credential: { id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
      response: { clientDataJSON: bufToB64u(cred.response.clientDataJSON), attestationObject: bufToB64u(cred.response.attestationObject),
        transports: cred.response.getTransports ? cred.response.getTransports() : [] },
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {} } });
  };
  H.loginPasskey = async function (next) {
    const o = await H.post("/mfa/webauthn/options");
    o.challenge = b64uToBuf(o.challenge); (o.allowCredentials || []).forEach((c) => (c.id = b64uToBuf(c.id)));
    const a = await navigator.credentials.get({ publicKey: o });
    return H.post("/mfa/webauthn/verify", { next, credential: { id: a.id, rawId: bufToB64u(a.rawId), type: a.type,
      response: { clientDataJSON: bufToB64u(a.response.clientDataJSON), authenticatorData: bufToB64u(a.response.authenticatorData),
        signature: bufToB64u(a.response.signature), userHandle: a.response.userHandle ? bufToB64u(a.response.userHandle) : null },
      clientExtensionResults: a.getClientExtensionResults ? a.getClientExtensionResults() : {} } });
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

  // ---------- ranked lists: top N + search ----------
  (function () {
    let saved = null; try { saved = localStorage.getItem("hsco.toplist.n"); } catch (e) {}
    document.querySelectorAll("[data-toplist]").forEach((box) => {
      const sel = box.querySelector(".tl-bar select"), q = box.querySelector(".tl-bar input");
      const items = [...box.querySelectorAll("li[data-q]")], none = box.querySelector("li.tl-none");
      const cnt = box.querySelector("[data-tl-count]");
      if (!sel || !q) return;
      if (saved !== null && sel.querySelector(`option[value="${saved}"]`)) sel.value = saved;
      function apply() {
        const n = +sel.value, s = q.value.trim().toLowerCase();
        let shown = 0;
        items.forEach((li, i) => { const ok = s ? li.dataset.q.includes(s) : (!n || i < n); li.hidden = !ok; if (ok) shown++; });
        if (none) none.hidden = shown > 0;
        if (cnt) cnt.textContent = shown;
      }
      sel.addEventListener("change", () => { try { localStorage.setItem("hsco.toplist.n", sel.value); } catch (e) {}
        document.querySelectorAll("[data-toplist] .tl-bar select").forEach((o) => { if (o !== sel && o.querySelector(`option[value="${sel.value}"]`)) { o.value = sel.value; o.dispatchEvent(new Event("tl:sync")); } });
        apply(); });
      sel.addEventListener("tl:sync", apply);
      q.addEventListener("input", apply);
      q.addEventListener("keydown", (e) => { if (e.key === "Escape") { q.value = ""; apply(); } });
      apply();
    });
  })();

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
  H.chart = (id, cfg) => {
    const el = document.getElementById(id); if (!el || !window.Chart) return;
    cfg.options = cfg.options || {}; cfg.options.scales = cfg.options.scales || {};
    if (cfg.type === "bar" || cfg.type === "line") {
      const ix = cfg.options.indexAxis === "y" ? "y" : "x";
      cfg.options.scales[ix] = Object.assign({ grid: { display: false } }, cfg.options.scales[ix] || {});
    }
    return new Chart(el, cfg);
  };
})();
