// HSCO → PDF (v1.13.1). Built in the browser from the app's own pages — headings, KPI cards, charts, ranked lists
// and tables (as real text tables, colours kept) — so nothing leaves the server.
//   HSCO.pdf(title)            this page: every tab, rows as filtered on screen
//   HSCO.pdfFull(periodKey)    the full report: cover + contents, then Overview, Details, Payroll leave sheet,
//                              Review queue, Punch exceptions, Locations and Reconciliation (each page loaded in a
//                              hidden frame and laid out completely)
// Charts are drawn again off-screen at print size with animation off (never half-drawn, never blank); ranked lists
// print everyone on them (not just the top N shown on screen). A4 landscape; header, footer, page numbers.
// Loaded on demand by app.js after vendor/jspdf + jspdf-autotable.
(function () {
  "use strict";
  const H = (window.HSCO = window.HSCO || {});
  const W = 297, PH = 210, M = 12, TOP = 18, BOTTOM = 14, CW = W - 2 * M, PXMM = 4;
  const NAVY = [31, 58, 95], INK = [16, 24, 40], GREY = [102, 112, 133], LINE = [228, 231, 236], SOFT = [249, 250, 251];
  const TONE = { bad: [217, 45, 32], warn: [181, 71, 8], good: [6, 118, 71] };
  const SKIP = ".info-i, svg, .tl-bar, [data-table-for], .tt, .tt-active, button, .sr-only, script, style, " +
               ".lg-info, .person > .av, .pdf-menu, .no-pdf, form, .drawer, .scrim, .chips";

  // the standard PDF fonts only know Western European characters: spell the rest out
  const MAP = { "−": "-", "–": "-", "—": "-", "→": "->", "←": "<-", "≥": ">=", "≤": "<=", "≈": "~", "▲": "+", "▼": "-",
    "✓": "v", "✔": "v", "✕": "x", "★": "*", "•": "·", "…": "...", "‘": "'", "’": "'", "“": '"', "”": '"', "₹": "Rs " };
  const clean = (s) => String(s ?? "").replace(/[   ]/g, " ").replace(/[−–—→←≥≤≈▲▼✓✔✕★•…‘’“”₹]/g, (c) => MAP[c])
    .replace(/[^\x00-\xff]/g, "").replace(/\s+/g, " ").trim();
  // screen-only hints ("click a bar to see who") are not printed
  const HINT = /\b(click|tap|hover|press|toolbar)\b/i;
  const noHint = (s) => String(s || "").split(" · ").filter((x) => !HINT.test(x)).join(" · ");
  const noHintText = (s) => String(s || "").split(/(?<=[.!?])\s+/).filter((x) => !HINT.test(x)).join(" ");
  const frames = (n) => new Promise((ok) => { const f = () => (n-- > 0 ? requestAnimationFrame(f) : ok()); f(); });
  const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));

  function rgb(css) {
    const m = /rgba?\(([^)]+)\)/.exec(css || ""); if (!m) return null;
    const p = m[1].split(",").map((x) => parseFloat(x)); const a = p.length > 3 ? p[3] : 1;
    if (a < 0.05) return null;
    const v = p.slice(0, 3).map((x) => Math.round(x * a + 255 * (1 - a)));
    return v[0] > 249 && v[1] > 249 && v[2] > 249 ? null : v;
  }

  // ------------------------------------------------------------------------------------------------ the writer
  // the firm's logo as a PNG (any format the browser shows: SVG, PNG, JPEG, WebP), or null when there is none
  async function loadLogo() {
    if (window.HSCO_LOGO === false) return null;          // no logo set: nothing to fetch
    try {
      // same-origin URL straight into the image (a blob: URL is refused by the page's img-src policy)
      const im = await new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = "/branding/logo?v=" + Date.now(); });
      const h = 180, w0 = im.naturalWidth || 600, h0 = im.naturalHeight || 180, w = Math.max(1, Math.round(w0 * h / h0));
      const cv = document.createElement("canvas"); cv.width = Math.min(w, 3000); cv.height = h;
      cv.getContext("2d").drawImage(im, 0, 0, cv.width, h);
      return { data: cv.toDataURL("image/png"), ratio: cv.width / h };
    } catch (e) { return null; }
  }

  function Writer(title) {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4", compress: true });
    const P = { doc, y: TOP, title, toc: [] };
    P.font = (size, style, color) => { doc.setFont("helvetica", style || "normal"); doc.setFontSize(size); doc.setTextColor(...(color || INK)); };
    P.pending = null;
    P.need = (h) => {
      const sec = P.pending; P.pending = null;
      const extra = sec ? 12 : 0;
      const broke = P.y + h + extra > PH - BOTTOM;
      if (broke) P.page();
      if (sec) { doc.setFillColor(238, 243, 255); doc.roundedRect(M, P.y, CW, 8, 1.5, 1.5, "F");
        P.font(11, "bold", [29, 60, 150]); doc.text(sec, M + 3, P.y + 5.4); P.y += 12; P.lastSection = sec; }
      return broke;
    };
    P.page = () => { doc.addPage(); P.y = TOP; };
    return P;
  }

  // ------------------------------------------------------------------------------------------------ one page of the app
  // win = the window that shows it (this one, or a hidden frame); root = its main.content
  async function render(P, win, root, opts) {
    const doc = P.doc, d = win.document;
    const cs = (el) => win.getComputedStyle(el);
    const shown = (el) => !!el && el.nodeType === 1 && el.getClientRects().length > 0 && cs(el).visibility !== "hidden" && !el.hidden;
    const txt = (el) => {
      if (!el) return "";
      const parts = [];
      const walk = (n) => { for (const ch of n.childNodes) {
        if (ch.nodeType === 3) parts.push(ch.nodeValue);
        else if (ch.nodeType === 1 && !ch.hidden && !ch.classList.contains("hidden") && !ch.matches(SKIP)) walk(ch);
      } };
      walk(el);
      return clean(parts.join(" ")).replace(/\s+([,.;:%)])/g, "$1").replace(/([(])\s+/g, "$1");
    };
    const toneOf = (el) => ["bad", "warn", "good"].find((t) => el.classList.contains(t)) || "";
    const tabLabels = {};

    function heading(t, sub) {
      if (opts.chapter && clean(opts.chapter) !== clean(t)) {
        const rest = String(sub || "").split(" · ").filter((x) => clean(x) !== clean(opts.chapter)).join(" · ");
        sub = [t, rest].filter(Boolean).join(" · "); t = opts.chapter;
      }
      P.need(18);
      P.font(17, "bold", NAVY); doc.text(clean(t), M, P.y + 6);
      if (opts.chapter) P.toc.push({ title: clean(t), page: doc.getNumberOfPages() });
      P.y += 9;
      if (sub) { P.font(9, "normal", GREY); const ls = doc.splitTextToSize(noHint(sub), CW); doc.text(ls, M, P.y + 3); P.y += ls.length * 4; }
      P.y += 4;
    }
    function section(t) { P.pending = clean(t); }     // drawn by the next P.need(), with its first content
    function cardTitle(t, sub, x, w) {
      x = x ?? M; w = w ?? CW;
      P.font(11, "bold"); const tt = doc.splitTextToSize(clean(t), w)[0] || "";
      doc.text(tt, x, P.y + 4);
      const s = noHint(sub || "");
      if (s) { const tw = doc.getTextWidth(tt); P.font(8.5, "normal", GREY); const r = doc.splitTextToSize(s, Math.max(20, w - tw - 3))[0]; doc.text(r, x + tw + 3, P.y + 4); }
      P.y += 7;
    }
    function note(t, x, w) {
      t = clean(noHintText(t)); if (!t) return;
      P.font(8, "italic", GREY); const ls = doc.splitTextToSize(t, w ?? CW);
      // a short note may use the bottom margin (above the footer line) rather than start a page of its own
      const h = ls.length * 3.6 + 2;
      if (!(P.pending === null && ls.length <= 2 && P.y + h <= PH - 10)) P.need(h);
      doc.text(ls, x ?? M, P.y + 3); P.y += ls.length * 3.6 + 3;
    }

    // KPI cards / small figures: boxes in a row (wraps)
    // it.l label · it.v value (it.s its small grey part) · it.u grey line under the value · it.f description · it.c comparison with last month
    function boxes(items, big) {
      if (!items.length) return;
      const per = Math.min(items.length, big ? 4 : 6), gap = 3, bw = (CW - gap * (per - 1)) / per;
      const extra = (it) => (it.u ? 1 : 0) + (it.f && big ? 1 : 0) + (it.c ? 1 : 0);
      const lines = Math.max(...items.map(extra));
      const bh = (big ? 17 : 13) + lines * 3.6 + 2;
      items.forEach((it, i) => {
        const col = i % per;
        if (col === 0) { if (i) P.y += bh + gap; P.need(bh); }
        const x = M + col * (bw + gap);
        doc.setDrawColor(...LINE); doc.setFillColor(...(big ? [255, 255, 255] : SOFT)); doc.roundedRect(x, P.y, bw, bh, 2, 2, "FD");
        P.font(big ? 8 : 7, "normal", GREY); doc.text(doc.splitTextToSize(it.l, bw - 6)[0] || "", x + 3, P.y + (big ? 5.5 : 4.5));
        P.font(big ? 15 : 11, "bold", TONE[it.tone] || INK); const vt = doc.splitTextToSize(it.v, bw - 6)[0] || "";
        doc.text(vt, x + 3, P.y + (big ? 13 : 10.5));
        if (it.s) {                                   // the value's small grey part, as on the page (e.g. "81 days")
          const vw = doc.getTextWidth(vt); P.font(big ? 10 : 8, "normal", GREY);
          doc.text(doc.splitTextToSize(it.s, Math.max(10, bw - 8 - vw))[0] || "", x + 4.5 + vw, P.y + (big ? 13 : 10.5));
        }
        let ly = P.y + (big ? 17.5 : 14);
        const line = (t, size, color, style) => { P.font(size, style || "normal", color); doc.text(doc.splitTextToSize(t, bw - 6)[0] || "", x + 3, ly); ly += 3.6; };
        if (it.u) line(it.u, 7.5, GREY, "bold");
        if (big && it.f) line(it.f, 7, GREY);
        if (it.c) line(it.c, 6.8, it.c.startsWith("+") || it.c.startsWith("-") ? [52, 64, 84] : GREY);
      });
      P.y += bh + 5;
    }
    const fOf = (k) => { const fs = [...k.querySelectorAll(":scope > .f")]; return { f: txt(fs.find((x) => !x.classList.contains("cmp")) || null), c: txt(k.querySelector(":scope > .f.cmp")) }; };
    const vOf = (k) => {                              // a KPI value and its <small> part, apart
      const v = k.querySelector(".v"); if (!v) return { v: "", s: "" };
      const sm = v.querySelector(":scope > small"); if (!sm) return { v: txt(v), s: "" };
      const cl = v.cloneNode(true); cl.querySelector(":scope > small").remove();
      return { v: txt(cl), s: txt(sm) };
    };
    const kpis = (el) => boxes([...el.querySelectorAll(":scope > .kpi")].filter(shown).map((k) => ({ l: txt(k.querySelector(".l")), ...vOf(k), ...fOf(k), tone: toneOf(k) })), true);
    const minis = (el) => boxes([...el.querySelectorAll(":scope > .mini")].filter(shown).map((k) => ({ l: txt(k.querySelector(".l")), v: txt(k.querySelector(".v")), u: txt(k.querySelector(":scope > .u")), c: txt(k.querySelector(":scope > .f")), tone: toneOf(k) })), false);

    // a chart drawn again off-screen at print size (wMM × hMM), animation off, then placed as an image
    function chartPng(canvas, wMM, hMM) {
      const src = win.Chart && win.Chart.getChart(canvas);
      if (!src || !window.Chart) return null;
      const w = Math.round(wMM * PXMM), h = Math.round(hMM * PXMM);
      const holder = document.createElement("div");
      holder.style.cssText = `position:fixed;left:-30000px;top:0;width:${w}px;height:${h}px`;
      const cv = document.createElement("canvas"); cv.style.width = w + "px"; cv.style.height = h + "px"; cv.width = w; cv.height = h;
      holder.appendChild(cv); document.body.appendChild(holder);
      const o = src.config.options || {};
      const plugins = Object.assign({}, o.plugins, { tooltip: { enabled: false } });
      const scales = {};
      Object.entries(o.scales || {}).forEach(([k, v]) => { scales[k] = Object.assign({}, v, { ticks: Object.assign({ maxRotation: 0, autoSkipPadding: 6 }, v && v.ticks) }); });
      const opts = Object.assign({}, o, { animation: false, responsive: false, maintainAspectRatio: false, devicePixelRatio: 3,
        events: [], onClick: null, onHover: null, plugins, scales });
      let png = null;
      try {
        const ch = new window.Chart(cv, { type: src.config.type, data: src.config.data, options: opts, plugins: src.config.plugins || [] });
        png = ch.toBase64Image("image/png", 1); ch.destroy();
      } catch (e) { console.error("chart for PDF", e); }
      holder.remove();
      return png;
    }
    function chartBlock(canvas, title, sub) {
      const h = 78;
      P.need(h + (title ? 8 : 0) + 2);
      if (title) cardTitle(title, sub);
      const png = chartPng(canvas, CW, h);
      if (png) doc.addImage(png, "PNG", M, P.y, CW, h, undefined, "FAST");
      P.y += h + 5;
    }

    // DOM table → PDF table (rows as filtered on screen, hidden columns left out, colours kept)
    function table(t) {
      const vis = (c) => cs(c).display !== "none";
      const cellOf = (c, head) => {
        const out = { content: txt(c), styles: {} };
        if (c.colSpan > 1) out.colSpan = c.colSpan;
        if (head) return out;
        const badges = [...c.querySelectorAll(".badge, .neg, .pos, .score")];
        const bg = rgb(cs(c).backgroundColor);
        if (bg && (!out.content || c.closest("table.heat"))) out.styles.fillColor = bg;       // colour cells (heatmaps)
        const whole = badges.length && badges.map(txt).join(" ") === out.content;            // the badge IS the value
        if (whole) { const fg = rgb(cs(badges[0]).color); if (fg) out.styles.textColor = fg; if (out.content.length <= 28) out.styles.fontStyle = "bold"; }
        else if (c.classList.contains("neg") || c.classList.contains("pos")) { const fg = rgb(cs(c).color); if (fg) out.styles.textColor = fg; }
        if (c.classList.contains("num")) out.styles.halign = "right";
        if (c.classList.contains("strong") || c.tagName === "TH") out.styles.fontStyle = "bold";
        return out;
      };
      const rowsOf = (sec, head) => !sec ? [] : [...sec.rows].filter((r) => r.style.display !== "none" && shown(r))
        .map((r) => [...r.cells].filter(vis).map((c) => cellOf(c, head)));
      let head = t.tHead ? rowsOf(t.tHead, true).slice(-1) : [];
      let body = rowsOf(t.tBodies[0]).filter((r) => r.length && !(r.length === 1 && !r[0].content));
      let foot = rowsOf(t.tFoot);
      if (!body.length) return;
      const heat = t.classList.contains("hm");
      const plain = (rows) => rows.every((r) => !r.some((x) => x.colSpan));
      if (head[0] && !heat && plain(body) && plain(head) && plain(foot)) {
        const keep = head[0].map((h, i) => !!h.content || body.some((r) => r[i] && r[i].content) || foot.some((r) => r[i] && r[i].content));
        if (keep.includes(false)) { const k = (r) => r.filter((_x, i) => keep[i]); head = head.map(k); body = body.map(k); foot = foot.map(k); }
      }
      const ncol = Math.max(head[0] ? head[0].length : 0, ...body.map((r) => r.length));
      const fs = ncol > 30 ? 5.4 : ncol > 18 ? 6.2 : ncol > 10 ? 7 : 7.8;
      // columns of short values (dates, times, groups, numbers) keep to one line; the long ones take the rest
      const columnStyles = {};
      if (!heat && plain(body)) {
        const widthOf = (i) => Math.max(...[...body.slice(0, 400), ...foot].map((r) => (r[i] && r[i].content ? r[i].content.length : 0)));
        // exact width of a short column in the PDF font: values (bold where the cell is bold, totals always bold) and
        // the header's longest word (the header itself may wrap)
        const mm = (t, bold) => { doc.setFont("helvetica", bold ? "bold" : "normal"); doc.setFontSize(fs); return doc.getTextWidth(t || ""); };
        const est = (i) => 2.8 + Math.max(
          ...body.slice(0, 400).map((r) => (r[i] ? mm(r[i].content, r[i].styles && r[i].styles.fontStyle === "bold") : 0)),
          ...foot.map((r) => (r[i] ? mm(r[i].content, true) : 0)),
          ...head.map((r) => (r[i] ? Math.max(...String(r[i].content).split(" ").map((w) => mm(w, true))) : 0)));
        let cand = [...Array(ncol).keys()].filter((i) => { const w = widthOf(i); return w > 0 && w <= 28; }).sort((a, b) => widthOf(a) - widthOf(b));
        while (cand.length && cand.reduce((a, i) => a + est(i), 0) > CW * 0.62) cand.pop();      // drop the widest until it fits
        // fixed widths only when a wide column is left to take the rest (a table of only short columns spans the page)
        if (cand.length < ncol) cand.forEach((i) => (columnStyles[i] = { cellWidth: est(i) }));
        else [...Array(ncol).keys()].forEach((i) => (columnStyles[i] = { minCellWidth: est(i) }));
      }
      P.need(20);
      doc.autoTable({
        head, body, foot, startY: P.y, margin: { left: M, right: M, top: TOP, bottom: BOTTOM }, theme: "grid",
        styles: { font: "helvetica", fontSize: fs, cellPadding: heat ? 0.6 : { top: 0.9, bottom: 0.9, left: 1.2, right: 1.2 },
                  overflow: "linebreak", lineColor: LINE, lineWidth: 0.12, textColor: INK, valign: "middle", minCellHeight: heat ? 3.4 : 0 },
        headStyles: { fillColor: NAVY, textColor: 255, fontStyle: "bold", fontSize: fs, halign: "left", valign: "bottom" },
        footStyles: { fillColor: [242, 244, 247], textColor: INK, fontStyle: "bold" },
        alternateRowStyles: heat ? {} : { fillColor: [251, 251, 252] },
        showHead: "everyPage", showFoot: "lastPage", rowPageBreak: "avoid", columnStyles,
        horizontalPageBreak: ncol > 42, horizontalPageBreakRepeat: 0,
      });
      P.y = doc.lastAutoTable.finalY + 6;
    }

    // ranked list → small table with EVERY person on it (not only the top N shown on screen)
    const listItems = (ul) => !ul ? [] : [...ul.children].filter((li) => li.tagName === "LI" && txt(li) && !li.classList.contains("tl-none") &&
      (li.dataset.match !== undefined ? li.dataset.match === "1" : shown(li)));
    function listRows(ul) {
      const right = (li) => {
        const r = li.querySelector(".r"); if (!r) return "";
        const bs = [...r.querySelectorAll(".badge")], rest = r.cloneNode(true); rest.querySelectorAll(".badge").forEach((x) => x.remove());
        const parts = [txt(rest), ...bs.map(txt)].filter(Boolean);
        const st = { halign: "right", fontStyle: "bold" };
        if (!txt(rest) && bs.length) { const fg = rgb(cs(bs[0]).color); if (fg) st.textColor = fg; }
        return { content: parts.join(" · "), styles: st };
      };
      return listItems(ul).map((li) => {
        const p = li.querySelector(".person");
        if (p) return [txt(li.querySelector(".rank")), txt(p.querySelector("b")), txt(p.querySelector(":scope > div > span")), right(li)];
        const c = li.cloneNode(true); c.querySelectorAll(".r").forEach((x) => x.remove());
        const sev = c.querySelector(":scope > .badge");
        let first = "";
        if (sev) { const fg = rgb(cs(li.querySelector(":scope > .badge")).color); first = { content: txt(sev), styles: { textColor: fg || INK, fontStyle: "bold" } }; sev.remove(); }
        return [first, { content: txt(c), styles: li.classList.contains("muted") ? { textColor: GREY, fontStyle: "italic" } : {} }, "", right(li)];
      });
    }
    function listTable(rows, x, w) {
      doc.autoTable({ body: rows, startY: P.y, margin: { left: x, right: W - x - w, top: TOP, bottom: BOTTOM }, theme: "plain",
        styles: { fontSize: 7.8, cellPadding: { top: 0.9, bottom: 0.9, left: 1, right: 1 }, textColor: INK, lineColor: LINE,
                  lineWidth: { top: 0, right: 0, bottom: 0.12, left: 0 } },
        columnStyles: { 0: { cellWidth: rows.some((r) => r[0] && r[0].content && isNaN(+r[0].content)) ? 16 : 6, textColor: GREY }, 2: { textColor: GREY } },
        rowPageBreak: "avoid" });
      P.y = doc.lastAutoTable.finalY + 1.5;
    }
    const footText = (box) => { if (!box || !box.querySelector(".tl-foot")) return "";
      const n = listItems(box.querySelector("ul.list")).length, sum = box.querySelector(".tl-foot [data-tl-sum]");
      return `${n} ${n === 1 ? "person" : "people"}` + (sum ? ` · total ${txt(sum.parentElement)}` : ""); };
    function list(ul) {
      const items = listItems(ul);
      if (!items.length) return;
      if (items.every((li) => li.classList.contains("muted"))) { items.forEach((li) => note(txt(li))); return; }
      P.need(14);
      listTable(listRows(ul), M, CW);
      const ft = footText(ul.closest("[data-toplist]")); if (ft) note(ft);
    }
    function shares(ol) {
      const rows = [...ol.querySelectorAll("li")].filter(shown).map((li) => {
        const tot = li.classList.contains("tot") || li.classList.contains("head"), sw = li.querySelector(".sl i"), col = sw && rgb(cs(sw).backgroundColor);
        const lab = (li.classList.contains("child") ? "    " : "") + txt(li.querySelector(".sl"));      // WFH / On Duty under Permission
        return [{ content: "", styles: col ? { fillColor: col } : {} }, { content: lab, styles: tot ? { fontStyle: "bold" } : {} },
                { content: txt(li.querySelector(".sv b")), styles: { halign: "right", fontStyle: "bold" } },
                { content: txt(li.querySelector(".sv small")), styles: { halign: "right", textColor: GREY } }];
      });
      P.need(12 + rows.length * 5);
      doc.autoTable({ head: [["", "Where", { content: "People", styles: { halign: "right" } }, { content: "Punches / days · share", styles: { halign: "right" } }]],
        body: rows, startY: P.y, margin: { left: M, right: M + CW / 3, top: TOP, bottom: BOTTOM }, theme: "plain",
        headStyles: { textColor: GREY, fontStyle: "bold", fontSize: 7.5 }, columnStyles: { 0: { cellWidth: 3 } },
        styles: { fontSize: 8.3, cellPadding: 1.2, lineColor: LINE, lineWidth: { top: 0, right: 0, bottom: 0.12, left: 0 } } });
      P.y = doc.lastAutoTable.finalY + 4;
    }

    // colour key of a heatmap: swatch + label, wrapped across the page
    function legend(el) {
      const items = [...el.querySelectorAll("button.lg")].map((b) => ({ c: rgb(cs(b.querySelector("i")).backgroundColor) || LINE, t: txt(b) || clean(b.textContent) }));
      if (!items.length) return;
      P.need(8);
      let x = M; P.font(7.5, "normal", INK);
      items.forEach((it) => {
        const w = doc.getTextWidth(it.t) + 8;
        if (x + w > W - M) { x = M; P.y += 5; }
        doc.setFillColor(...it.c); doc.setDrawColor(...LINE); doc.rect(x, P.y + 0.6, 3.4, 3.4, "FD");
        doc.text(it.t, x + 4.6, P.y + 3.4); x += w + 3;
      });
      P.y += 7;
    }
    function navList(first) {
      const rows = [];
      for (let a = first; a && a.matches("a.nav-item"); a = a.nextElementSibling) {
        const c = a.cloneNode(true); c.querySelectorAll(".r").forEach((x) => x.remove());
        rows.push(["", txt(c), "", { content: txt(a.querySelector(".r")) || "done", styles: { halign: "right", fontStyle: "bold" } }]);
      }
      P.need(8 + rows.length * 5);
      listTable(rows, M, CW / 2);
      P.y += 3;
    }
    function body(el) {
      for (const c of el.children) {
        if (!shown(c) || c.matches(SKIP)) continue;
        if (c.matches(".chartbox")) { const cv = c.querySelector("canvas"); if (cv) chartBlock(cv); }
        else if (c.matches("canvas")) chartBlock(c);
        else if (c.matches("table")) table(c);
        else if (c.matches(".tw")) { const t = c.querySelector("table"); if (t) table(t); }
        else if (c.matches("ul.list")) list(c);
        else if (c.matches("ul.shares")) shares(c);
        else if (c.matches(".minis")) minis(c);
        else if (c.matches(".kpis")) kpis(c);
        else if (c.matches(".tl-foot")) continue;                       // printed by list()
        else if (c.matches(".steps")) note([...c.querySelectorAll(".step")].map((s) => `${txt(s.querySelector(".t"))}: ${txt(s.querySelector(".d"))}`).join("   |   "));
        else if (c.matches(".hm-legend")) legend(c);
        else if (c.matches(".legend, .cal")) continue;
        else if (c.matches("a.nav-item")) { if (!(c.previousElementSibling && c.previousElementSibling.matches("a.nav-item"))) navList(c); }
        else if (c.matches("p, .help, .alert")) note(txt(c));
        else if (c.children.length && [...c.childNodes].some((n) => n.nodeType === 3 && n.nodeValue.trim())) note(txt(c));   // mixed text
        else if (c.children.length) body(c);
        else if (txt(c)) note(txt(c));
      }
    }
    const titleOf = (card) => { const hd = card.querySelector(":scope > .hd"); return hd ? [txt(hd.querySelector("h2")), txt(hd.querySelector(".sub"))] : ["", ""]; };
    const onlyChart = (c) => c.matches(".card") && c.querySelectorAll("canvas").length === 1 && !c.querySelector("table, ul.list, ul.shares, .minis");
    function card(el) {
      const [t, sub] = titleOf(el);
      // a card that is only a chart: title and chart always on the same page
      if (onlyChart(el)) { chartBlock(el.querySelector("canvas"), P.pending && clean(t) === P.pending ? "" : t, sub); return; }
      const same = P.pending ? clean(t) === P.pending : false;
      // the title stays with the start of what follows (a share list or small figures are kept whole)
      const sh = el.querySelector("ul.shares"), mi = el.querySelector(".minis");
      const first = sh ? 16 + sh.querySelectorAll("li").length * 5.2 : mi ? 30 : 28;
      if (t && !same) { P.need(first + 8); cardTitle(t, sub); }
      [...el.children].filter((c) => !c.matches(".hd")).forEach((c) => { if (shown(c)) body(c.matches(".bd") ? c : { children: [c] }); });
      P.y += 3;
    }
    // cards side by side on screen: charts (or short lists) side by side in the PDF too; anything else one after the other
    function grid(el) {
      const cards = [...el.children].filter(shown);
      const onlyList = (c) => c.matches(".card") && c.querySelector("ul.list") && !c.querySelector("canvas, table, ul.shares, .minis");
      const gap = 6, n = cards.length, w = (CW - gap * (n - 1)) / n;
      if (n > 1 && cards.every(onlyChart)) {
        const h = n > 2 ? 62 : 72;
        P.need(h + 10);
        const y0 = P.y;
        cards.forEach((c, i) => {
          const x = M + i * (w + gap), [t, sub] = titleOf(c);
          P.y = y0; cardTitle(t, sub, x, w);
          const png = chartPng(c.querySelector("canvas"), w, h);
          if (png) doc.addImage(png, "PNG", x, P.y, w, h, undefined, "FAST");
        });
        P.y = y0 + 7 + h + 6;
        return;
      }
      if (n > 1 && cards.every(onlyList)) {
        const rows = cards.map((c) => listRows(c.querySelector("ul.list")));
        const hNeed = 9 + Math.max(...rows.map((r) => r.length)) * 4.9 + 8;
        if (hNeed <= PH - TOP - BOTTOM - 4) {
          P.need(hNeed);
          const y0 = P.y; let bottom = y0;
          cards.forEach((c, i) => {
            const x = M + i * (w + gap), [t, sub] = titleOf(c);
            P.y = y0; cardTitle(t, sub, x, w);
            const its = listItems(c.querySelector("ul.list"));
            if (its.every((li) => li.classList.contains("muted"))) { P.font(8, "italic", GREY); doc.text(doc.splitTextToSize(txt(its[0]), w), x, P.y + 3); P.y += 6; }
            else listTable(rows[i], x, w);
            const ft = footText(c.querySelector("[data-toplist]"));
            if (ft) { P.font(7.5, "italic", GREY); doc.text(doc.splitTextToSize(ft, w)[0], x, P.y + 3); P.y += 5; }
            bottom = Math.max(bottom, P.y);
          });
          P.y = bottom + 5;
          return;
        }
      }
      cards.forEach((c) => (c.matches(".card") ? card(c) : walk(c)));
    }
    function walk(el) {
      for (const c of el.children) {
        if (c.matches(".tabs")) { c.querySelectorAll("[data-tab]").forEach((b) => (tabLabels[b.dataset.tab] = txt(b).replace(/\s\d+$/, ""))); continue; }
        if (c.matches("section.pane")) { if (c.children.length) { section(tabLabels[c.dataset.pane] || c.dataset.pane); walk(c); } continue; }
        if (!shown(c) || c.matches(".crumbs, .toasts, aside, " + SKIP)) continue;
        if (c.matches(".ph")) heading(txt(c.querySelector("h1")), txt(c.querySelector(".sub")));
        else if (c.matches(".kpis")) kpis(c);
        else if (c.matches(".minis")) minis(c);
        else if (c.matches(".card")) card(c);
        else if (c.matches(".grid")) grid(c);
        else if (c.matches(".alert, p")) note(txt(c));
        else if (c.matches("table")) table(c);
        else if (c.children.length) walk(c);
      }
    }

    // lay out every tab, then read the page
    d.body.classList.add("pdf-all");
    try {
      await frames(2);
      walk(root);
    } finally {
      d.body.classList.remove("pdf-all");
    }
  }

  // ------------------------------------------------------------------------------------------------ finishing
  function finish(P, cover) {
    const doc = P.doc, n = doc.getNumberOfPages(), when = new Date();
    const stamp = when.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
    const org = clean(window.HSCO_ORG || "HSCO");
    if (cover) {                                    // contents on the cover, now that the page numbers are known
      doc.setPage(1);
      let y = 100;
      P.font(12, "bold", NAVY); doc.text("Contents", M + 20, y); y += 9;
      P.toc.forEach((t, i) => {
        P.font(11, "normal", INK); doc.text(`${i + 1}.  ${t.title}`, M + 20, y);
        doc.setDrawColor(...LINE); doc.setLineDashPattern([0.6, 1.2], 0); doc.line(M + 100, y - 1, M + 186, y - 1); doc.setLineDashPattern([], 0);
        P.font(11, "bold", INK); doc.text(String(t.page), M + 196, y, { align: "right" });
        doc.link(M + 18, y - 4.5, 182, 6.5, { pageNumber: t.page });
        y += 7.5;
      });
    }
    for (let i = 1; i <= n; i++) {
      doc.setPage(i);
      doc.setFillColor(...NAVY); doc.rect(0, 0, W, 3, "F");
      if (cover && i === 1) continue;
      let tx = M;
      if (P.logo) { const lh = 7, lw = Math.min(lh * P.logo.ratio, 60); doc.addImage(P.logo.data, "PNG", M, 5, lw, lh, "logo", "FAST"); tx = M + lw + 4; }
      else { P.font(8.5, "bold", NAVY); doc.text(org, M, 10); tx = M + doc.getTextWidth(org) + 4; }
      P.font(8.5, "normal", GREY); doc.text(clean(P.title), tx, 10);
      doc.text("Generated " + clean(stamp), W - M, 10, { align: "right" });
      doc.setDrawColor(...LINE); doc.line(M, PH - 9, W - M, PH - 9);
      P.font(7.5, "normal", GREY); doc.text("Attendance MIS - data stays on this server", M, PH - 5);
      doc.text(`Page ${i} of ${n}`, W - M, PH - 5, { align: "right" });
    }
    const z = (x) => String(x).padStart(2, "0");
    const file = `Attendance MIS - ${clean(P.title).replace(/[\\/:*?"<>|]+/g, "-")} - ${when.getFullYear()}${z(when.getMonth() + 1)}${z(when.getDate())}-${z(when.getHours())}${z(when.getMinutes())}.pdf`;
    doc.save(file);
    return { pages: n, file, toc: P.toc };
  }
  function coverPage(P, heading, sub) {
    const doc = P.doc, org = clean(window.HSCO_ORG || "HSCO");
    doc.setFillColor(238, 243, 255); doc.rect(0, 3, W, 76, "F");
    if (P.logo) { const lh = 15, lw = Math.min(lh * P.logo.ratio, 120); doc.addImage(P.logo.data, "PNG", M + 20, 13, lw, lh, "logo", "FAST"); }
    else { P.font(12, "bold", NAVY); doc.text(org, M + 20, 28); }
    P.font(26, "bold", NAVY); doc.text(clean(heading), M + 20, 46);
    P.font(13, "normal", INK); doc.text(clean(sub), M + 20, 57);
    const stamp = new Date().toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
    P.font(9, "normal", GREY);
    doc.text("Generated " + clean(stamp) + " from the app's figures at that moment. Attendance MIS - data stays on this server.", M + 20, 68);
  }

  const busy = (on) => {
    let c = document.querySelector(".pdf-cover");
    if (on && !c) { c = document.createElement("div"); c.className = "pdf-cover"; c.innerHTML = "<div>Preparing the PDF…<small></small></div>"; document.body.append(c); }
    if (!on && c) c.remove();
    return c;
  };

  // this page, as it is on screen (filters kept), every tab
  H.pdf = async function (title) {
    const P = Writer(title);
    busy(true);
    try {
      P.logo = await loadLogo();
      await render(P, window, document.querySelector("main.content") || document.body, {});
      return finish(P, false);
    } finally { busy(false); }
  };

  // the full report: every report page of the month, each loaded in a hidden frame
  H.REPORT_PAGES = [["/", "Overview"], ["/insights", "Details"], ["/periods/{k}", "Payroll leave sheet"], ["/flags", "Review queue"],
                    ["/exceptions", "Punch exceptions"], ["/locations", "Locations"], ["/reconciliation", "Reconciliation"]];
  async function framePage(url) {
    const f = document.createElement("iframe");
    f.style.cssText = "position:fixed;left:-30000px;top:0;width:1440px;height:1000px;border:0";
    f.setAttribute("aria-hidden", "true");
    const loaded = new Promise((ok, bad) => { f.onload = ok; setTimeout(() => bad(new Error("timed out loading " + url)), 60000); });
    f.src = url;
    document.body.appendChild(f);
    try { await loaded; } catch (e) { f.remove(); throw e; }
    for (let i = 0; i < 100 && !(f.contentWindow.HSCO && f.contentWindow.document.readyState === "complete"); i++) await sleep(100);
    await sleep(250);
    return f;
  }
  H.pdfFull = async function (key, label) {
    const P = Writer(`Full report ${label || key}`);
    const c = busy(true);
    try {
      P.logo = await loadLogo();
      coverPage(P, "Attendance MIS report", label || key);
      for (const [path, name] of H.REPORT_PAGES) {
        const url = path.includes("{k}") ? path.replace("{k}", key) : `${path}?period=${encodeURIComponent(key)}`;
        if (c) c.querySelector("small").textContent = " — " + name;
        // a page that is slow to come up (busy server) is tried again rather than left out of the report
        let f = null, root = null;
        for (let attempt = 0; attempt < 3 && !(root && root.querySelector(".ph h1")); attempt++) {
          if (f) { f.remove(); f = null; await sleep(500); }
          try { f = await framePage(url); } catch (e) { console.warn(e); continue; }
          root = f.contentDocument && f.contentDocument.querySelector("main.content");
        }
        if (!f) continue;
        if (root && root.querySelector(".ph h1")) {
          P.page();
          await render(P, f.contentWindow, root, { chapter: name });
        }
        f.remove();
      }
      return finish(P, true);
    } finally { busy(false); }
  };
})();
