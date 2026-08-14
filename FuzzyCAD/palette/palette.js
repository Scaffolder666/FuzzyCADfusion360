/* FuzzyCAD palette — the async-collaboration sidebar (think Overleaf's review
 * panel). The modeling happens directly in the viewport (pick a tool, drag on
 * the model); this panel is the list of *open questions* a collaborator resolves.
 *
 * Bridge with Fusion:
 *   panel -> Fusion : adsk.fusionSendData(action, jsonString)  (Promise)
 *   Fusion -> panel : window.fusionJavaScriptHandler.handle(action, jsonString)
 */
(function () {
  "use strict";

  var state = { marks: [] };
  var HASAXIS = { move: true, rotate: true, extrude: false, fillet: false };

  function send(action, data) {
    if (window.adsk && typeof window.adsk.fusionSendData === "function") {
      return window.adsk.fusionSendData(action, JSON.stringify(data || {}));
    }
    console.log("[FuzzyCAD send]", action, data);
    return Promise.resolve("{}");
  }

  window.fusionJavaScriptHandler = {
    handle: function (action, dataString) {
      try {
        var data = dataString ? JSON.parse(dataString) : {};
        if (action === "state") { state.marks = data.marks || []; render(); }
      } catch (e) { return JSON.stringify({ ok: false, error: String(e) }); }
      return JSON.stringify({ ok: true });
    },
  };

  var els = {};
  function cache() {
    els.tools = Array.prototype.slice.call(document.querySelectorAll(".tool"));
    els.marks = document.getElementById("marks");
    els.empty = document.getElementById("empty");
    els.openCount = document.getElementById("openCount");
    els.doneCount = document.getElementById("doneCount");
  }

  function fmt(n) { return (Math.round(n * 10) / 10).toString(); }

  var adjustTimer = null;
  function adjustLive(id, value) {
    if (adjustTimer) clearTimeout(adjustTimer);
    adjustTimer = setTimeout(function () { send("adjust", { id: id, value: value }); }, 40);
  }

  function render() {
    var open = state.marks.filter(function (m) { return !m.resolved; }).length;
    els.openCount.textContent = open + " open";
    els.doneCount.textContent = (state.marks.length - open) + " decided";
    els.empty.style.display = state.marks.length ? "none" : "block";
    els.marks.innerHTML = "";

    state.marks.forEach(function (m) {
      var li = document.createElement("li");
      li.className = "mark" + (m.resolved ? " mark--resolved" : "");

      var head = document.createElement("div");
      head.className = "mark__head";

      var dot = document.createElement("span");
      dot.className = "mark__dot";

      var name = document.createElement("span");
      name.className = "mark__label";
      name.textContent = m.label || (m.tool.charAt(0).toUpperCase() + m.tool.slice(1));

      var focus = iconBtn("◎", "Focus camera", function () { send("focus", { id: m.id }); });
      var del = iconBtn("×", "Delete", function () { send("delete", { id: m.id }); });
      del.classList.add("mark__icon--del");

      head.appendChild(dot); head.appendChild(name);
      head.appendChild(focus); head.appendChild(del);
      li.appendChild(head);

      var meta = document.createElement("div");
      meta.className = "mark__meta";
      meta.textContent = "fuzzy " + m.tool +
        (HASAXIS[m.tool] ? " · axis " + m.axis : "") +
        " · " + fmt(m.value) + m.unit;
      li.appendChild(meta);

      // adjust the proposed amount (Overleaf-style: tweak, then resolve)
      var srow = document.createElement("div");
      srow.className = "mark__slider";
      var slider = document.createElement("input");
      slider.type = "range";
      slider.min = m.min; slider.max = m.max; slider.step = m.step; slider.value = m.value;
      slider.disabled = !!m.resolved;
      var out = document.createElement("span");
      out.className = "mark__out";
      out.textContent = fmt(m.value) + m.unit;
      slider.addEventListener("input", function () {
        out.textContent = fmt(parseFloat(slider.value)) + m.unit;
        meta.textContent = "fuzzy " + m.tool +
          (HASAXIS[m.tool] ? " · axis " + m.axis : "") +
          " · " + fmt(parseFloat(slider.value)) + m.unit;
        adjustLive(m.id, parseFloat(slider.value));
      });
      srow.appendChild(slider); srow.appendChild(out);
      li.appendChild(srow);

      var foot = document.createElement("div");
      foot.className = "mark__foot";
      if (m.resolved) {
        var badge = document.createElement("span");
        badge.className = "badge badge--done";
        badge.textContent = "✓ decided";
        var reopen = document.createElement("button");
        reopen.className = "linkbtn";
        reopen.textContent = "Reopen";
        reopen.addEventListener("click", function () { send("reopen", { id: m.id }); });
        foot.appendChild(badge); foot.appendChild(reopen);
      } else {
        var status = document.createElement("span");
        status.className = "badge badge--open";
        status.textContent = "needs a decision";
        var go = document.createElement("button");
        go.className = "resolve-btn";
        go.textContent = "Decide";
        go.addEventListener("click", function () { send("resolve", { id: m.id }); });
        foot.appendChild(status); foot.appendChild(go);
      }
      li.appendChild(foot);
      els.marks.appendChild(li);
    });
  }

  function iconBtn(glyph, title, fn) {
    var b = document.createElement("button");
    b.className = "mark__icon"; b.textContent = glyph; b.title = title;
    b.addEventListener("click", fn);
    return b;
  }

  function wire() {
    els.tools.forEach(function (btn) {
      btn.addEventListener("click", function () {
        send("tool", { tool: btn.getAttribute("data-tool") });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    cache(); wire(); render(); send("ready", {});
  });
})();
