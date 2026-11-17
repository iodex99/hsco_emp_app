// Small, dependency-free helpers: sortable/filterable tables, confirms, WebAuthn, chart defaults.
(function () {
  "use strict";
  const csrf = () => (document.querySelector('meta[name="csrf"]') || {}).content || "";
  window.HSCO = window.HSCO || {};

  // ---------- tables: <table data-sortable> + <input data-filter="#tableId">
  function cellVal(td) {
    const v = td.getAttribute("data-v");
    const t = v !== null ? v : td.textContent.trim();
    const n = parseFloat(t.replace(/,/g, ""));
    return isNaN(n) || !/^-?[\d.,]+$/.test(t.replace(/\s/g, "")) ? t.toLowerCase() : n;
  }
  document.querySelectorAll("table[data-sortable]").forEach((tbl) => {
    tbl.querySelectorAll("th").forEach((th, idx) => {
      if (th.hasAttribute("data-nosort")) return;
      th.classList.add("sortable");
      th.addEventListener("click", () => {
        const body = tbl.tBodies[0];
        const rows = Array.from(body.rows);
        const dir = th.dataset.dir === "asc" ? "desc" : "asc";
        tbl.querySelectorAll("th").forEach((x) => delete x.dataset.dir);
        th.dataset.dir = dir;
        rows.sort((a, b) => {
          const x = cellVal(a.cells[idx]), y = cellVal(b.cells[idx]);
          if (x === y) return 0;
          return (x > y ? 1 : -1) * (dir === "asc" ? 1 : -1);
        });
        rows.forEach((r) => body.appendChild(r));
      });
    });
  });
  document.querySelectorAll("input[data-filter]").forEach((inp) => {
    const tbl = document.querySelector(inp.dataset.filter);
    const count = document.querySelector(inp.dataset.count || "#nope");
    const apply = () => {
      const q = inp.value.toLowerCase().trim();
      let n = 0;
      Array.from(tbl.tBodies[0].rows).forEach((r) => {
        const show = !q || r.textContent.toLowerCase().includes(q);
        r.style.display = show ? "" : "none";
        if (show) n++;
      });
      if (count) count.textContent = n;
    };
    inp.addEventListener("input", apply);
  });
  document.querySelectorAll("select[data-colfilter]").forEach((sel) => {
    const tbl = document.querySelector(sel.dataset.colfilter);
    const col = parseInt(sel.dataset.col, 10);
    sel.addEventListener("change", () => {
      Array.from(tbl.tBodies[0].rows).forEach((r) => {
        const v = r.cells[col] ? r.cells[col].textContent.trim() : "";
        r.style.display = !sel.value || v === sel.value ? "" : "none";
      });
    });
  });
  document.querySelectorAll("form[data-confirm]").forEach((f) =>
    f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));
  document.querySelectorAll("select[data-nav]").forEach((s) =>
    s.addEventListener("change", () => { if (s.value) location.href = s.value; }));

  // ---------- WebAuthn helpers
  const b64uToBuf = (s) => {
    s = s.replace(/-/g, "+").replace(/_/g, "/"); while (s.length % 4) s += "=";
    return Uint8Array.from(atob(s), (c) => c.charCodeAt(0)).buffer;
  };
  const bufToB64u = (b) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  async function post(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
      body: JSON.stringify(body || {}), credentials: "same-origin" });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || j.detail || ("HTTP " + r.status));
    return j;
  }
  HSCO.registerPasskey = async function (label) {
    if (!window.PublicKeyCredential) throw new Error("This browser does not support security keys / passkeys (HTTPS required).");
    const o = await post("/security/webauthn/options");
    o.challenge = b64uToBuf(o.challenge); o.user.id = b64uToBuf(o.user.id);
    (o.excludeCredentials || []).forEach((c) => (c.id = b64uToBuf(c.id)));
    const cred = await navigator.credentials.create({ publicKey: o });
    const payload = { id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
      response: { clientDataJSON: bufToB64u(cred.response.clientDataJSON), attestationObject: bufToB64u(cred.response.attestationObject),
        transports: cred.response.getTransports ? cred.response.getTransports() : [] },
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {} };
    return post("/security/webauthn/register", { credential: payload, label: label });
  };
  HSCO.loginPasskey = async function (next) {
    const o = await post("/mfa/webauthn/options");
    o.challenge = b64uToBuf(o.challenge);
    (o.allowCredentials || []).forEach((c) => (c.id = b64uToBuf(c.id)));
    const a = await navigator.credentials.get({ publicKey: o });
    const payload = { id: a.id, rawId: bufToB64u(a.rawId), type: a.type,
      response: { clientDataJSON: bufToB64u(a.response.clientDataJSON), authenticatorData: bufToB64u(a.response.authenticatorData),
        signature: bufToB64u(a.response.signature), userHandle: a.response.userHandle ? bufToB64u(a.response.userHandle) : null },
      clientExtensionResults: a.getClientExtensionResults ? a.getClientExtensionResults() : {} };
    return post("/mfa/webauthn/verify", { credential: payload, next: next });
  };

  // ---------- charts
  HSCO.palette = ["#1f3a5f", "#2b6cb0", "#38a169", "#d69e2e", "#c53030", "#6b46c1", "#319795", "#dd6b20", "#718096", "#b83280"];
  if (window.Chart) {
    Chart.defaults.font.family = '"Segoe UI",system-ui,Arial,sans-serif';
    Chart.defaults.font.size = 11.5;
    Chart.defaults.color = "#475569";
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
    Chart.defaults.maintainAspectRatio = false;
  }
  HSCO.chart = function (id, cfg) { const el = document.getElementById(id); if (el && window.Chart) return new Chart(el, cfg); };
})();
