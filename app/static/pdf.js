// HSCO page → PDF (v1.13). Built in the browser from what the page shows — headings, KPI cards, charts (as images),
// lists and tables (as real text tables, colours kept) — so nothing leaves the server. Every tab of the page is
// included, one after the other. A4 landscape; header, footer and page numbers on every page.
// Loaded on demand by app.js ([data-pdf] buttons) after vendor/jspdf + jspdf-autotable.
(function () {
  "use strict";
  const H = (window.HSCO = window.HSCO || {});
  const W = 297, PH = 210, M = 12, TOP = 20, BOTTOM = 14, CW = W - 2 * M;
  const NAVY = [31, 58, 95], INK = [16, 24, 40], GREY = [102, 112, 133], LINE = [228, 231, 236], SOFT = [249, 250, 251];
  const TONE = { bad: [217, 45, 32], warn: [181, 71, 8], good: [6, 118, 71] };
  const SKIP = ".info-i, svg, .tl-bar, [data-table-for], .tt, .tt-active, button.tt-tog, .sr-only, script, style, .hm-legend, .lg-info, .person > .av";

  // the standard PDF fonts only know Western European characters: spell the rest out
  const MAP = { "−": "-", "–": "-", "—": "-", "→": "->", "←": "<-", "≥": ">=", "≤": "<=", "≈": "~", "▲": "+", "▼": "-", "✓": "v", "✔": "v",
    "✕": "x", "★": "*", "•": "·", "…": "...", " ": " ", " ": " ", " ": " ", "‘": "'", "’": "'", "“": '"', "”": '"', "₹": "Rs " };
  const clean = (s) => String(s ?? "").replace(/[−–—→←≥≤≈▲▼✓✔✕★•…   ‘’“”₹]/g, (c) => MAP[c]).replace(/[^\x00-\xff]/g, "").replace(/\s+/g, " ").trim();
  // text of an element: its text pieces with a space between elements ("High" + "Unapplied leave" → "High Unapplied
  // leave"), without the "i" buttons, icons, toolbars and hidden parts
  function txt(el) {
    if (!el) return "";
    if (el.nodeType === 3) return clean(el.nodeValue);
    const parts = [];
    const walk = (n) => { for (const ch of n.childNodes) {
      if (ch.nodeType === 3) parts.push(ch.nodeValue);
      else if (ch.nodeType === 1 && !ch.hidden && !ch.classList.contains("hidden") && !ch.matches(SKIP)) walk(ch);
    } };
    walk(el);
    return clean(parts.join(" ")).replace(/\s+([,.;:%)])/g, "$1").replace(/([(])\s+/g, "$1");
  }
  const shown = (el) => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  function rgb(css) {
    const m = /rgba?\(([^)]+)\)/.exec(css || ""); if (!m) return null;
    const p = m[1].split(",").map((x) => parseFloat(x)); const a = p.length > 3 ? p[3] : 1;
    if (a < 0.05) return null;
    const v = p.slice(0, 3).map((x) => Math.round(x * a + 255 * (1 - a)));
    return v[0] > 250 && v[1] > 250 && v[2] > 250 ? null : v;
  }
  const frames = (n) => new Promise((ok) => { const f = () => (n-- > 0 ? requestAnimationFrame(f) : ok()); f(); });

  H.pdf = async function (title) {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4", compress: true });
    const root = document.querySelector("main.content") || document.body;
    // show every tab and draw the charts sharp (2×) while the PDF is made
    const cover = document.createElement("div"); cover.className = "pdf-cover"; cover.innerHTML = "<div>Preparing the PDF…</div>";
    document.body.append(cover); document.body.classList.add("pdf-all");
    const charts = Object.values(H.charts || {});
    charts.forEach((c) => { c.$dpr = c.options.devicePixelRatio; c.options.devicePixelRatio = 2; c.resize(); });
    await frames(3);
    let y = TOP;
    const tabLabels = {};
    const need = (h) => { if (y + h > PH - BOTTOM) { doc.addPage(); y = TOP; return true; } return false; };
    const font = (size, style, color) => { doc.setFont("helvetica", style || "normal"); doc.setFontSize(size); doc.setTextColor(...(color || INK)); };

    function heading(t, sub) {
      need(16); font(17, "bold", NAVY); doc.text(clean(t), M, y + 5);
      if (sub) { font(9, "normal", GREY); doc.text(doc.splitTextToSize(clean(sub), CW), M, y + 11); y += 6; }
      y += 11;
    }
    function section(t) {
      need(22); doc.setFillColor(238, 243, 255); doc.roundedRect(M, y, CW, 8, 1.5, 1.5, "F");
      font(11, "bold", [29, 60, 150]); doc.text(clean(t), M + 3, y + 5.4); y += 12;
    }
    function cardTitle(t, sub, h) {
      need(h || 18); font(11.5, "bold"); doc.text(clean(t), M, y + 4);
      if (sub) { const tw = doc.getTextWidth(clean(t)); font(8.5, "normal", GREY); doc.text(clean(sub), M + tw + 3, y + 4); }
      y += 7;
    }
    function note(t) { t = clean(t); if (!t) return; font(8, "italic", GREY); const ls = doc.splitTextToSize(t, CW); need(ls.length * 3.6 + 2); doc.text(ls, M, y + 3); y += ls.length * 3.6 + 3; }

    // KPI cards / small figures: boxes in a row (wraps)
    function boxes(items, big) {
      if (!items.length) return;
      const per = Math.min(items.length, big ? 4 : 6), gap = 3, bw = (CW - gap * (per - 1)) / per, bh = big ? 24 : 15;
      items.forEach((it, i) => {
        const col = i % per;
        if (col === 0) { if (i) y += bh + gap; need(bh); }
        const x = M + col * (bw + gap);
        doc.setDrawColor(...LINE); doc.setFillColor(...(big ? [255, 255, 255] : SOFT)); doc.roundedRect(x, y, bw, bh, 2, 2, "FD");
        font(big ? 8 : 7, "normal", GREY); doc.text(doc.splitTextToSize(it.l, bw - 6)[0] || "", x + 3, y + (big ? 5.5 : 4.5));
        font(big ? 15 : 11, "bold", TONE[it.tone] || INK); doc.text(doc.splitTextToSize(it.v, bw - 6)[0] || "", x + 3, y + (big ? 13 : 10.5));
        if (big && it.f) { font(7, "normal", GREY); doc.text(doc.splitTextToSize(it.f, bw - 6).slice(0, 2), x + 3, y + 18); }
      });
      y += bh + 5;
    }
    const toneOf = (el) => ["bad", "warn", "good"].find((t) => el.classList.contains(t)) || "";
    function kpis(el) { boxes([...el.querySelectorAll(":scope > .kpi")].filter(shown).map((k) => ({ l: txt(k.querySelector(".l")), v: txt(k.querySelector(".v")), f: txt(k.querySelector(".f")), tone: toneOf(k) })), true); }
    function minis(el) { boxes([...el.querySelectorAll(":scope > .mini")].filter(shown).map((k) => ({ l: txt(k.querySelector(".l")), v: txt(k.querySelector(".v")), tone: toneOf(k) })), false); }

    function chartImg(canvas, x, w, maxH) {
      const ch = window.Chart && Chart.getChart(canvas); if (!ch || !canvas.width) return 0;
      let h = w * canvas.height / canvas.width;
      if (h > maxH) { w = w * maxH / h; h = maxH; }
      doc.addImage(ch.toBase64Image("image/png", 1), "PNG", x, y, w, h, undefined, "FAST");
      return h;
    }
    function chart(canvas) {
      const ch = window.Chart && Chart.getChart(canvas); if (!ch) return;
      const h = Math.min(CW * canvas.height / canvas.width, 92); need(h + 2);
      y += chartImg(canvas, M, CW, 92) + 4;
    }

    // DOM table → PDF table (rows as shown: filters, search and hidden columns respected; colours kept)
    function table(t) {
      const vis = (c) => getComputedStyle(c).display !== "none";
      const cellOf = (c, head) => {
        const out = { content: txt(c), colSpan: c.colSpan > 1 ? c.colSpan : undefined, styles: {} };
        const badge = !head && c.querySelector(".badge");
        const cs = getComputedStyle(badge || c);
        const bg = rgb(cs.backgroundColor); if (bg && !head) out.styles.fillColor = bg;
        const fg = rgb(cs.color); if (fg && !head && (badge || c.classList.contains("neg") || c.classList.contains("pos") || c.querySelector(".neg,.pos"))) out.styles.textColor = rgb(getComputedStyle(badge || c.querySelector(".neg,.pos") || c).color) || fg;
        if (c.classList.contains("num")) out.styles.halign = "right";
        if (c.classList.contains("strong") || c.querySelector("b.strong,.strong")) out.styles.fontStyle = "bold";
        return out;
      };
      const rowsOf = (sec, head) => !sec ? [] : [...sec.rows].filter((r) => r.style.display !== "none" && shown(r)).map((r) => [...r.cells].filter(vis).map((c) => cellOf(c, head)));
      const head = t.tHead ? rowsOf(t.tHead, true).slice(-1) : [];
      const body = rowsOf(t.tBodies[0]).filter((r) => r.length);
      const foot = rowsOf(t.tFoot);
      if (!body.length) return;
      const ncol = Math.max(head[0] ? head[0].length : 0, ...body.map((r) => r.length));
      const fs = ncol > 30 ? 5.5 : ncol > 18 ? 6.3 : ncol > 11 ? 7.2 : 8.2;
      need(18);
      doc.autoTable({
        head, body, foot, startY: y, margin: { left: M, right: M, top: TOP, bottom: BOTTOM }, theme: "grid",
        styles: { font: "helvetica", fontSize: fs, cellPadding: ncol > 30 ? 0.8 : 1.4, overflow: "linebreak", lineColor: LINE, lineWidth: 0.15, textColor: INK, valign: "middle" },
        headStyles: { fillColor: NAVY, textColor: 255, fontStyle: "bold", fontSize: fs, halign: "left" },
        footStyles: { fillColor: [242, 244, 247], textColor: INK, fontStyle: "bold" },
        alternateRowStyles: { fillColor: [252, 252, 253] },
        showHead: "everyPage", showFoot: "lastPage", rowPageBreak: "avoid",
        horizontalPageBreak: ncol > 42, horizontalPageBreakRepeat: 0,
      });
      y = doc.lastAutoTable.finalY + 6;
    }
    // ranked list / any list of people → small table
    function list(ul, x, w) {
      const items = [...ul.children].filter((li) => li.tagName === "LI" && shown(li) && txt(li));
      // a list with nobody on it ("None this month.") is a line of text, not a table
      if (items.every((li) => li.classList.contains("muted"))) { items.forEach((li) => note(txt(li))); return; }
      const right = (li) => ({ content: txt(li.querySelector(".r")), styles: { halign: "right", fontStyle: "bold" } });
      const rows = items.map((li) => {
        const p = li.querySelector(".person");
        if (p) return [txt(li.querySelector(".rank")), txt(p.querySelector("b")), txt(p.querySelector(":scope > div > span")), right(li)];
        const c = li.cloneNode(true); c.querySelectorAll(".r").forEach((x) => x.remove());      // e.g. "High · Unapplied leave … | 8 people"
        return ["", { content: txt(c), styles: li.classList.contains("muted") ? { textColor: GREY, fontStyle: "italic" } : {} }, "", right(li)];
      });
      if (!rows.length) return;
      doc.autoTable({ body: rows, startY: y, margin: { left: x ?? M, right: W - (x ?? M) - (w ?? CW), top: TOP, bottom: BOTTOM }, theme: "plain",
        styles: { fontSize: 8, cellPadding: 1.2, textColor: INK, lineColor: LINE, lineWidth: { top: 0, right: 0, bottom: 0.15, left: 0 } },
        columnStyles: { 0: { cellWidth: 7, textColor: GREY }, 2: { textColor: GREY } }, rowPageBreak: "avoid" });
      y = doc.lastAutoTable.finalY + 2;
    }
    function shares(ol) {
      const rows = [...ol.querySelectorAll("li")].filter(shown).map((li) => {
        const tot = li.classList.contains("tot"), st = tot ? { fontStyle: "bold" } : {};
        const sw = li.querySelector(".sl i"), col = sw && rgb(getComputedStyle(sw).backgroundColor);
        return [{ content: "", styles: col ? { fillColor: col } : {} }, { content: txt(li.querySelector(".sl")), styles: st },
                { content: txt(li.querySelector(".sv b")), styles: { halign: "right", fontStyle: "bold" } },
                { content: txt(li.querySelector(".sv small")), styles: { halign: "right", textColor: GREY } }];
      });
      doc.autoTable({ head: [["", "Where", { content: "Punches", styles: { halign: "right" } }, { content: "Share · people", styles: { halign: "right" } }]], body: rows, startY: y, margin: { left: M, right: M + CW / 3, top: TOP, bottom: BOTTOM }, theme: "plain",
        headStyles: { textColor: GREY, fontStyle: "bold", fontSize: 7.5 }, columnStyles: { 0: { cellWidth: 3 } },
        styles: { fontSize: 8.5, cellPadding: 1.3, lineColor: LINE, lineWidth: { top: 0, right: 0, bottom: 0.15, left: 0 } } });
      y = doc.lastAutoTable.finalY + 4;
    }

    function body(el) {
      for (const c of el.children) {
        if (!shown(c) || c.matches(SKIP)) continue;
        if (c.matches("canvas")) chart(c);
        else if (c.matches(".chartbox")) { const cv = c.querySelector("canvas"); if (cv) chart(cv); }
        else if (c.matches("table")) table(c);
        else if (c.matches(".tw")) { const t = c.querySelector("table"); if (t) table(t); }
        else if (c.matches("ul.list")) list(c);
        else if (c.matches("ul.shares")) shares(c);
        else if (c.matches(".minis")) minis(c);
        else if (c.matches(".kpis")) kpis(c);
        else if (c.matches(".tl-foot, p, .help, .alert")) note(txt(c));
        else if (c.matches(".legend, .chips, .steps")) { if (c.matches(".steps")) note([...c.querySelectorAll(".step")].map((s) => txt(s)).join("   |   ")); }
        else if (c.children.length) body(c);
        else if (txt(c)) note(txt(c));
      }
    }
    function card(el) {
      const hd = el.querySelector(":scope > .hd");
      const t = hd ? txt(hd.querySelector("h2")) : "", sub = hd ? txt(hd.querySelector(".sub")) : "";
      if (t) cardTitle(t, sub, 30);
      [...el.children].filter((c) => c !== hd).forEach((c) => (shown(c) ? body(c.matches(".bd") ? c : { children: [c] }) : null));
      y += 3;
    }
    // a row of short ranked lists side by side (as on screen)
    function listColumns(cards) {
      const lis = (c) => [...c.querySelectorAll("ul.list > li")].filter((li) => shown(li) && txt(li));
      const maxRows = Math.max(...cards.map((c) => lis(c).length));
      const h = 9 + maxRows * 5.4 + 8;
      if (h > PH - TOP - BOTTOM - 4) return false;
      need(h);
      const gap = 6, w = (CW - gap * (cards.length - 1)) / cards.length, y0 = y;
      let bottom = y0;
      cards.forEach((c, i) => {
        const x = M + i * (w + gap), hd = c.querySelector(".hd");
        y = y0; font(10.5, "bold"); doc.text(doc.splitTextToSize(txt(hd && hd.querySelector("h2")), w)[0] || "", x, y + 4); y += 7;
        const ul = c.querySelector("ul.list");
        if (lis(c).every((li) => li.classList.contains("muted"))) { font(8, "italic", GREY); doc.text(doc.splitTextToSize(txt(lis(c)[0]), w), x, y + 3); y += 6; }
        else list(ul, x, w);
        const f = c.querySelector(".tl-foot"); if (f && shown(f)) { font(7.5, "italic", GREY); doc.text(doc.splitTextToSize(txt(f), w)[0], x, y + 3); y += 5; }
        bottom = Math.max(bottom, y);
      });
      y = bottom + 5;
      return true;
    }
    // a row of chart cards side by side (as on screen); anything else one after the other
    function grid(el) {
      const cards = [...el.children].filter(shown);
      if (cards.length > 1 && cards.every((c) => c.matches(".card") && c.querySelector("ul.list") && !c.querySelector("canvas, table, ul.shares, .minis")) && listColumns(cards)) return;
      const allCharts = cards.length > 1 && cards.every((c) => c.matches(".card") && c.querySelector("canvas") && !c.querySelector("table, ul.list, ul.shares"));
      if (!allCharts) { cards.forEach((c) => (c.matches(".card") ? card(c) : walk(c))); return; }
      const gap = 6, w = (CW - gap * (cards.length - 1)) / cards.length;
      const hs = cards.map((c) => { const cv = c.querySelector("canvas"); return Math.min(w * cv.height / cv.width, 80); });
      need(Math.max(...hs) + 10);
      const y0 = y;
      cards.forEach((c, i) => {
        const x = M + i * (w + gap), hd = c.querySelector(".hd");
        y = y0; font(10.5, "bold"); doc.text(doc.splitTextToSize(txt(hd && hd.querySelector("h2")), w)[0] || "", x, y + 4);
        y += 7; chartImg(c.querySelector("canvas"), x, w, 80);
      });
      y = y0 + 7 + Math.max(...hs) + 6;
    }
    function walk(el) {
      for (const c of el.children) {
        if (c.matches(".tabs")) { c.querySelectorAll("[data-tab]").forEach((b) => (tabLabels[b.dataset.tab] = txt(b).replace(/\s\d+$/, ""))); continue; }
        if (c.matches("section.pane")) { if (!c.children.length) continue; section(tabLabels[c.dataset.pane] || c.dataset.pane); walk(c); continue; }
        if (!shown(c) || c.matches(".crumbs, .toasts, aside, .scrim, .drawer, form:not(:has(table)), " + SKIP)) continue;
        if (c.matches(".ph")) heading(txt(c.querySelector("h1")), txt(c.querySelector(".sub")));
        else if (c.matches(".kpis")) kpis(c);
        else if (c.matches(".minis")) minis(c);
        else if (c.matches(".card")) card(c);
        else if (c.matches(".grid")) grid(c);
        else if (c.matches(".alert")) note(txt(c));
        else if (c.matches("p")) note(txt(c));
        else if (c.children.length) walk(c);
      }
    }

    try {
      walk(root);
      const n = doc.getNumberOfPages(), when = new Date();
      const stamp = when.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
      for (let i = 1; i <= n; i++) {
        doc.setPage(i);
        doc.setFillColor(...NAVY); doc.rect(0, 0, W, 3, "F");
        font(8.5, "bold", NAVY); doc.text(clean(window.HSCO_ORG || "HSCO"), M, 10);
        font(8.5, "normal", GREY); doc.text(clean(title), M + doc.getTextWidth(clean(window.HSCO_ORG || "HSCO")) + 4, 10);
        doc.text("Generated " + clean(stamp), W - M, 10, { align: "right" });
        doc.setDrawColor(...LINE); doc.line(M, PH - 9, W - M, PH - 9);
        font(7.5, "normal", GREY); doc.text("Attendance & Leave - data stays on this server", M, PH - 5);
        doc.text(`Page ${i} of ${n}`, W - M, PH - 5, { align: "right" });
      }
      const z = (x) => String(x).padStart(2, "0");
      const file = `HSCO ${clean(title).replace(/[\\/:*?"<>|]+/g, "-")} ${when.getFullYear()}${z(when.getMonth() + 1)}${z(when.getDate())}-${z(when.getHours())}${z(when.getMinutes())}.pdf`;
      doc.save(file);
      return { pages: n, file };
    } finally {
      document.body.classList.remove("pdf-all"); cover.remove();
      charts.forEach((c) => { c.options.devicePixelRatio = c.$dpr; c.resize(); });
    }
  };
})();
