/* FuzzyCAD Inspector — a fallback admin surface in the palette.
 *
 * Shows a live status snapshot (open marks by type, ghosted bodies, any stray
 * graphics groups) and offers a one-click Repair that runs the visual authority
 * (sweep stray graphics + restore every ghosted body + redraw) by hand. The
 * per-item Locate / Delete reuse the panel's existing focus / reject actions.
 *
 * Self-contained: it defines its own send() so it does not depend on palette.js
 * internals, and talks to fuzzycad_inspector.py via inspectorData / repairViewport.
 */
(function () {
  "use strict";

  function send(action, data) {
    if (window.adsk && typeof window.adsk.fusionSendData === "function") {
      return window.adsk.fusionSendData(action, JSON.stringify(data || {}));
    }
    return Promise.resolve("{}");
  }

  var GLYPH = {
    move: "⇄", rotate: "↻", scale: "⤢", scale_axis: "⇥",
    axis_rotate: "⟳", extrude: "⤒", fillet: "◜", hole: "⊙",
    note: "◈", compare: "⑂"
  };
  var TLABEL = { need_input: "NEED INPUT", constraint: "CONSTRAINT", conflict: "CONFLICT" };

  var els = {};
  function el(id) { return document.getElementById(id); }

  function stat(n, label, warn) {
    var d = document.createElement("div");
    d.className = "stat" + (warn ? " stat--warn" : "");
    var v = document.createElement("div"); v.className = "stat__n"; v.textContent = n;
    var l = document.createElement("div"); l.className = "stat__l"; l.textContent = label;
    d.appendChild(v); d.appendChild(l);
    return d;
  }

  function renderData(data) {
    data = data || {};
    var c = data.counts || {};
    var stray = data.stray || [];
    var strayTotal = stray.reduce(function (s, g) { return s + (g.count || 0); }, 0);

    els.stats.innerHTML = "";
    els.stats.appendChild(stat(c.open || 0, "Open questions"));
    els.stats.appendChild(stat(c.need_input || 0, "Need Input"));
    els.stats.appendChild(stat(c.conflict || 0, "Conflict"));
    els.stats.appendChild(stat(c.ghosted || 0, "Ghosted bodies"));
    els.stats.appendChild(stat(strayTotal, "Stray graphics", strayTotal > 0));

    var meta = (data.storage || "");
    if (stray.length) {
      meta += " · leftover: " + stray.map(function (g) {
        return g.id.replace("FuzzyCAD_", "") + "(" + g.count + ")";
      }).join(", ");
    }
    els.meta.textContent = meta;

    var items = data.items || [];
    els.items.innerHTML = "";
    if (!items.length) {
      var none = document.createElement("div");
      none.className = "insp__none";
      none.textContent = "Nothing generated yet.";
      els.items.appendChild(none);
      return;
    }
    items.forEach(function (it) {
      var li = document.createElement("li");
      li.className = "irow";
      var g = document.createElement("span"); g.className = "irow__g";
      g.textContent = GLYPH[it.tool] || "◆";
      var l = document.createElement("span"); l.className = "irow__l";
      l.textContent = it.label;
      var t = document.createElement("span");
      t.className = "irow__t t--" + it.mtype;
      t.textContent = TLABEL[it.mtype] || it.mtype;
      var loc = document.createElement("button");
      loc.className = "irow__b"; loc.type = "button"; loc.textContent = "Locate";
      loc.addEventListener("click", function () { send("focus", { id: it.id }); });
      var del = document.createElement("button");
      del.className = "irow__b irow__b--del"; del.type = "button"; del.textContent = "Delete";
      del.addEventListener("click", function () {
        send("reject", { id: it.id }); setTimeout(refresh, 120);
      });
      li.appendChild(g); li.appendChild(l); li.appendChild(t);
      li.appendChild(loc); li.appendChild(del);
      els.items.appendChild(li);
    });
  }

  function refresh() {
    var p = send("inspectorData", {});
    if (p && typeof p.then === "function") {
      p.then(function (resp) {
        try { renderData(JSON.parse(resp || "{}")); } catch (e) {}
      });
    }
  }

  function open() { els.panel.classList.add("open"); refresh(); }
  function close() { els.panel.classList.remove("open"); }

  function repair() {
    els.repair.classList.add("busy");
    els.repair.textContent = "Repairing…";
    var p = send("repairViewport", {});
    var done = function () {
      els.repair.classList.remove("busy");
      els.repair.textContent = "Repaired ✓";
      setTimeout(function () { els.repair.textContent = "Repair viewport"; }, 1400);
      refresh();
    };
    if (p && typeof p.then === "function") p.then(done, done); else done();
  }

  document.addEventListener("DOMContentLoaded", function () {
    els.panel = el("inspector");
    els.stats = el("inspStats");
    els.meta = el("inspMeta");
    els.items = el("inspItems");
    els.repair = el("inspRepair");
    var openBtn = el("openInspector");
    if (openBtn) openBtn.addEventListener("click", open);
    var closeBtn = el("inspClose");
    if (closeBtn) closeBtn.addEventListener("click", close);
    if (els.repair) els.repair.addEventListener("click", repair);
  });
})();
