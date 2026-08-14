/* FuzzyCAD sidebar — the async-collaboration panel (Overleaf-style).
 * A list of open questions (proposed fuzzy ops). Click a card to focus its
 * geometry; Accept turns it into real geometry; × discards it.
 *
 * Bridge:  panel -> Fusion : adsk.fusionSendData(action, jsonString)
 *          Fusion -> panel : window.fusionJavaScriptHandler.handle(action, jsonString)
 */
(function () {
  "use strict";

  var state = { marks: [] };

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
    els.marks = document.getElementById("marks");
    els.empty = document.getElementById("empty");
    els.count = document.getElementById("count");
  }

  var GLYPH = { move: "⇄", rotate: "↻", extrude: "⤒", fillet: "◜" };

  function render() {
    els.count.textContent = state.marks.length + (state.marks.length === 1 ? " open" : " open");
    els.empty.style.display = state.marks.length ? "none" : "block";
    els.marks.innerHTML = "";

    state.marks.forEach(function (m) {
      var li = document.createElement("li");
      li.className = "mark";
      li.title = "Click to focus this in the model";
      li.addEventListener("click", function () { send("focus", { id: m.id }); });

      var head = document.createElement("div");
      head.className = "mark__head";

      var glyph = document.createElement("span");
      glyph.className = "mark__glyph";
      glyph.textContent = GLYPH[m.tool] || "◆";

      var name = document.createElement("span");
      name.className = "mark__label";
      name.textContent = m.label || (m.tool.charAt(0).toUpperCase() + m.tool.slice(1));

      var del = document.createElement("button");
      del.className = "mark__icon mark__icon--del";
      del.textContent = "×"; del.title = "Discard";
      del.addEventListener("click", function (ev) {
        ev.stopPropagation(); send("delete", { id: m.id });
      });

      head.appendChild(glyph); head.appendChild(name); head.appendChild(del);
      li.appendChild(head);

      var meta = document.createElement("div");
      meta.className = "mark__meta";
      meta.textContent = m.summary;
      li.appendChild(meta);

      var foot = document.createElement("div");
      foot.className = "mark__foot";
      var status = document.createElement("span");
      status.className = "badge";
      status.textContent = "proposed — not final";
      var accept = document.createElement("button");
      accept.className = "accept-btn";
      accept.textContent = "Accept";
      accept.title = "Apply this to the real geometry";
      accept.addEventListener("click", function (ev) {
        ev.stopPropagation(); send("accept", { id: m.id });
      });
      foot.appendChild(status); foot.appendChild(accept);
      li.appendChild(foot);

      els.marks.appendChild(li);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    cache(); render(); send("ready", {});
  });
})();
