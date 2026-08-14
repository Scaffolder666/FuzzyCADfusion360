/* FuzzyCAD palette — a series of fuzzy operation tools.
 *
 * Bridge with Fusion:
 *   panel -> Fusion : adsk.fusionSendData(action, jsonString)  (Promise)
 *   Fusion -> panel : window.fusionJavaScriptHandler.handle(action, jsonString)
 */
(function () {
  "use strict";

  var state = { marks: [], tool: "move", axis: "Z" };

  var TOOL_SEL = {
    move: "Select a body (or a face of one) first.",
    rotate: "Select a body (or a face of one) first.",
    extrude: "Select a planar face first.",
    fillet: "Select an edge first.",
  };
  var TOOL_HASAXIS = { move: true, rotate: true, extrude: false, fillet: false };

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
        if (action === "state") { state.marks = data.marks || []; renderMarks(); }
      } catch (e) { return JSON.stringify({ ok: false, error: String(e) }); }
      return JSON.stringify({ ok: true });
    },
  };

  var els = {};
  function cache() {
    els.tools = Array.prototype.slice.call(document.querySelectorAll(".tool"));
    els.axes = Array.prototype.slice.call(document.querySelectorAll(".axis"));
    els.axisRow = document.getElementById("axisRow");
    els.label = document.getElementById("label");
    els.add = document.getElementById("add");
    els.selHint = document.getElementById("selHint");
    els.marks = document.getElementById("marks");
    els.count = document.getElementById("count");
    els.empty = document.getElementById("empty");
  }

  function refreshComposer() {
    els.axisRow.style.display = TOOL_HASAXIS[state.tool] ? "" : "none";
    els.selHint.textContent = TOOL_SEL[state.tool];
    var name = state.tool.charAt(0).toUpperCase() + state.tool.slice(1);
    els.add.textContent = "+ Add fuzzy " + state.tool + " at selection";
  }

  function fmt(n) { return (Math.round(n * 10) / 10).toString(); }

  // debounce slider -> Fusion so fast drags don't flood redraws
  var adjustTimer = null;
  function adjustLive(id, value) {
    if (adjustTimer) clearTimeout(adjustTimer);
    adjustTimer = setTimeout(function () {
      send("adjust", { id: id, value: value });
    }, 40);
  }

  function renderMarks() {
    els.count.textContent = String(state.marks.length);
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
      name.textContent = (m.label || (m.tool.charAt(0).toUpperCase() + m.tool.slice(1)));

      var tag = document.createElement("span");
      tag.className = "mark__tag";
      tag.textContent = m.tool + (m.axis && TOOL_HASAXIS[m.tool] ? " · " + m.axis : "");

      var focus = iconBtn("◎", "Focus camera", function () { send("focus", { id: m.id }); });
      var del = iconBtn("×", "Delete", function () { send("delete", { id: m.id }); });
      del.classList.add("mark__icon--del");

      head.appendChild(dot); head.appendChild(name);
      head.appendChild(tag); head.appendChild(focus); head.appendChild(del);
      li.appendChild(head);

      // slider row — easy, direct modification (no ranges to type)
      var srow = document.createElement("div");
      srow.className = "mark__slider";
      var slider = document.createElement("input");
      slider.type = "range";
      slider.min = m.min; slider.max = m.max; slider.step = m.step;
      slider.value = m.value;
      slider.disabled = !!m.resolved;
      var out = document.createElement("span");
      out.className = "mark__out";
      out.textContent = fmt(m.value) + m.unit;
      slider.addEventListener("input", function () {
        out.textContent = fmt(parseFloat(slider.value)) + m.unit;
        adjustLive(m.id, parseFloat(slider.value));
      });
      srow.appendChild(slider); srow.appendChild(out);
      li.appendChild(srow);

      // resolve row
      var rrow = document.createElement("div");
      rrow.className = "mark__resolve";
      if (m.resolved) {
        var badge = document.createElement("span");
        badge.className = "resolved-badge";
        badge.textContent = "✓ decided";
        var reopen = document.createElement("button");
        reopen.className = "linkbtn";
        reopen.textContent = "Reopen";
        reopen.addEventListener("click", function () { send("reopen", { id: m.id }); });
        rrow.appendChild(badge); rrow.appendChild(reopen);
      } else {
        var hint = document.createElement("span");
        hint.className = "fuzzy-hint";
        hint.textContent = "sketchy = not decided";
        var go = document.createElement("button");
        go.className = "resolve-btn";
        go.textContent = "Decide";
        go.addEventListener("click", function () { send("resolve", { id: m.id }); });
        rrow.appendChild(hint); rrow.appendChild(go);
      }
      li.appendChild(rrow);
      els.marks.appendChild(li);
    });
  }

  function iconBtn(glyph, title, fn) {
    var b = document.createElement("button");
    b.className = "mark__icon";
    b.textContent = glyph; b.title = title;
    b.addEventListener("click", fn);
    return b;
  }

  function wire() {
    els.tools.forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.tool = btn.getAttribute("data-tool");
        els.tools.forEach(function (b) { b.classList.remove("is-active"); });
        btn.classList.add("is-active");
        refreshComposer();
      });
    });
    els.axes.forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.axis = btn.getAttribute("data-axis");
        els.axes.forEach(function (b) { b.classList.remove("is-active"); });
        btn.classList.add("is-active");
      });
    });
    els.add.addEventListener("click", function () {
      send("add", { tool: state.tool, axis: state.axis, label: els.label.value.trim() })
        .then(function () { els.label.value = ""; });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    cache(); wire(); refreshComposer(); renderMarks(); send("ready", {});
  });
})();
