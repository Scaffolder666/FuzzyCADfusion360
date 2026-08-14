/* FuzzyCAD palette — plain JS + a tiny state store.
 *
 * Two-way bridge with Fusion:
 *   panel -> Fusion : adsk.fusionSendData(action, jsonString)  (returns a Promise)
 *   Fusion -> panel : window.fusionJavaScriptHandler.handle(action, jsonString)
 *
 * Swap this file for a React/Vite build later; the bridge stays the same.
 */
(function () {
  "use strict";

  var state = {
    marks: [],
    kind: "needs_input",
  };

  // --- bridge: panel -> Fusion ---------------------------------------------
  function send(action, data) {
    if (window.adsk && typeof window.adsk.fusionSendData === "function") {
      return window.adsk.fusionSendData(action, JSON.stringify(data || {}));
    }
    // Running in a plain browser (no Fusion host): log so the UI still works.
    console.log("[FuzzyCAD send]", action, data);
    return Promise.resolve("{}");
  }

  // --- bridge: Fusion -> panel ---------------------------------------------
  // Fusion calls this. Must return a string.
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

  // --- rendering -----------------------------------------------------------
  var els = {};
  function cache() {
    els.label = document.getElementById("label");
    els.add = document.getElementById("add");
    els.marks = document.getElementById("marks");
    els.count = document.getElementById("count");
    els.empty = document.getElementById("empty");
    els.kinds = Array.prototype.slice.call(document.querySelectorAll(".kind"));
  }

  function render() {
    els.count.textContent = String(state.marks.length);
    els.empty.style.display = state.marks.length ? "none" : "block";
    els.marks.innerHTML = "";

    state.marks.forEach(function (mark) {
      var li = document.createElement("li");
      li.className = "mark mark--" + mark.kind;

      var dot = document.createElement("span");
      dot.className = "mark__dot";

      var label = document.createElement("span");
      label.className = "mark__label";
      label.textContent = mark.label;

      var focus = document.createElement("button");
      focus.className = "mark__btn";
      focus.textContent = "Focus";
      focus.addEventListener("click", function () {
        send("focusMark", { id: mark.id });
      });

      var del = document.createElement("button");
      del.className = "mark__btn mark__btn--del";
      del.textContent = "×";
      del.title = "Delete mark";
      del.addEventListener("click", function () {
        send("deleteMark", { id: mark.id });
      });

      li.appendChild(dot);
      li.appendChild(label);
      li.appendChild(focus);
      li.appendChild(del);
      els.marks.appendChild(li);
    });
  }

  // --- events --------------------------------------------------------------
  function wire() {
    els.kinds.forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.kind = btn.getAttribute("data-kind");
        els.kinds.forEach(function (b) { b.classList.remove("is-active"); });
        btn.classList.add("is-active");
      });
    });

    els.add.addEventListener("click", function () {
      send("addMark", {
        label: els.label.value.trim(),
        kind: state.kind,
      }).then(function () {
        els.label.value = "";
      });
    });

    els.label.addEventListener("keydown", function (e) {
      if (e.key === "Enter") els.add.click();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    cache();
    wire();
    render();
    send("ready", {}); // ask Fusion to push current state
  });
})();
