/* FuzzyCAD palette — Open Range (Needs Input) representation.
 *
 * Bridge with Fusion:
 *   panel -> Fusion : adsk.fusionSendData(action, jsonString)  (Promise)
 *   Fusion -> panel : window.fusionJavaScriptHandler.handle(action, jsonString)
 */
(function () {
  "use strict";

  var state = { marks: [], axis: "Z" };

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
        if (action === "state") {
          state.marks = data.marks || [];
          render();
        }
      } catch (e) {
        return JSON.stringify({ ok: false, error: String(e) });
      }
      return JSON.stringify({ ok: true });
    },
  };

  var els = {};
  function cache() {
    els.label = document.getElementById("label");
    els.min = document.getElementById("min");
    els.max = document.getElementById("max");
    els.add = document.getElementById("add");
    els.marks = document.getElementById("marks");
    els.count = document.getElementById("count");
    els.empty = document.getElementById("empty");
    els.axes = Array.prototype.slice.call(document.querySelectorAll(".axis"));
  }

  function fmt(n) {
    return (Math.round(n * 10) / 10).toString();
  }

  function render() {
    els.count.textContent = String(state.marks.length);
    els.empty.style.display = state.marks.length ? "none" : "block";
    els.marks.innerHTML = "";

    state.marks.forEach(function (mark) {
      var resolved = mark.resolved !== null && mark.resolved !== undefined;
      var li = document.createElement("li");
      li.className = "mark" + (resolved ? " mark--resolved" : "");

      // header row: dot + label + value + focus + delete
      var head = document.createElement("div");
      head.className = "mark__head";

      var dot = document.createElement("span");
      dot.className = "mark__dot";

      var name = document.createElement("span");
      name.className = "mark__label";
      name.textContent = mark.label || "Angle";

      var val = document.createElement("span");
      val.className = "mark__val";
      val.textContent = resolved
        ? "θ = " + fmt(mark.resolved) + "° · " + mark.axis
        : "θ ∈ [" + fmt(mark.min) + "°, " + fmt(mark.max) + "°] · " + mark.axis;

      var focus = document.createElement("button");
      focus.className = "mark__icon";
      focus.title = "Focus camera";
      focus.textContent = "◎";
      focus.addEventListener("click", function () { send("focusMark", { id: mark.id }); });

      var del = document.createElement("button");
      del.className = "mark__icon mark__icon--del";
      del.title = "Delete";
      del.textContent = "×";
      del.addEventListener("click", function () { send("deleteMark", { id: mark.id }); });

      head.appendChild(dot);
      head.appendChild(name);
      head.appendChild(val);
      head.appendChild(focus);
      head.appendChild(del);
      li.appendChild(head);

      // resolve row
      var row = document.createElement("div");
      row.className = "mark__resolve";
      if (resolved) {
        var badge = document.createElement("span");
        badge.className = "resolved-badge";
        badge.textContent = "✓ resolved";
        var reopen = document.createElement("button");
        reopen.className = "linkbtn";
        reopen.textContent = "Reopen";
        reopen.addEventListener("click", function () { send("reopenMark", { id: mark.id }); });
        row.appendChild(badge);
        row.appendChild(reopen);
      } else {
        var input = document.createElement("input");
        input.type = "number";
        input.className = "resolve-input";
        input.step = "1";
        input.placeholder = "value °";
        var mid = (mark.min + mark.max) / 2;
        input.value = fmt(mid);
        var go = document.createElement("button");
        go.className = "resolve-btn";
        go.textContent = "Resolve";
        var fire = function () { send("resolveMark", { id: mark.id, value: parseFloat(input.value) }); };
        go.addEventListener("click", fire);
        input.addEventListener("keydown", function (e) { if (e.key === "Enter") fire(); });
        row.appendChild(input);
        row.appendChild(go);
      }
      li.appendChild(row);
      els.marks.appendChild(li);
    });
  }

  function wire() {
    els.axes.forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.axis = btn.getAttribute("data-axis");
        els.axes.forEach(function (b) { b.classList.remove("is-active"); });
        btn.classList.add("is-active");
      });
    });

    els.add.addEventListener("click", function () {
      send("addRange", {
        label: els.label.value.trim(),
        axis: state.axis,
        min: parseFloat(els.min.value),
        max: parseFloat(els.max.value),
      }).then(function () { els.label.value = ""; });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    cache();
    wire();
    render();
    send("ready", {});
  });
})();
