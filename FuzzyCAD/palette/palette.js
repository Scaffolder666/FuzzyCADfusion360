/* FuzzyCAD sidebar — the async-collaboration panel (Overleaf-style).
 *
 * Each card is an open question a teammate can act on:
 *   - editable value fields (Move X/Y/Z, angle, depth, radius) -> updates the 3D ghost
 *   - a status: Needs Input / Answered / Rejected
 *   - a comment thread
 *   - Apply, which turns it into real geometry
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

  var GLYPH = { move: "⇄", rotate: "↻", scale: "⤢", extrude: "⤒", fillet: "◜", note: "◈" };
  var STATUS_LABEL = { open: "Open", answered: "Answered" };
  var MTYPES = [
    { key: "need_input", label: "Need Input", glyph: "!" },
    { key: "constraint", label: "Constraint", glyph: "‖" },
    { key: "alternative", label: "Alternative", glyph: "⑂" }
  ];

  var editTimers = {};
  function editLive(id, key, value) {
    var k = id + ":" + key;
    if (editTimers[k]) clearTimeout(editTimers[k]);
    editTimers[k] = setTimeout(function () { send("edit", { id: id, key: key, value: value }); }, 120);
  }

  function stop(ev) { ev.stopPropagation(); }

  function render() {
    els.count.textContent = state.marks.length + (state.marks.length === 1 ? " open question" : " open questions");
    els.empty.style.display = state.marks.length ? "none" : "block";
    els.marks.innerHTML = "";

    state.marks.forEach(function (m) {
      var mtype = m.mtype || "need_input";
      var li = document.createElement("li");
      li.className = "mark mark--" + m.status + " type--" + mtype;
      li.title = "Click to focus this in the model";
      li.addEventListener("click", function () { send("focus", { id: m.id }); });

      // header
      var head = document.createElement("div");
      head.className = "mark__head";
      var glyph = document.createElement("span");
      glyph.className = "mark__glyph"; glyph.textContent = GLYPH[m.tool] || "◆";
      var name = document.createElement("span");
      name.className = "mark__label"; name.textContent = m.label || m.title;
      var mt = MTYPES.filter(function (t) { return t.key === mtype; })[0] || MTYPES[0];
      var typeTag = document.createElement("span");
      typeTag.className = "typetag typetag--" + mtype;
      typeTag.textContent = mt.glyph + " " + mt.label;
      head.appendChild(glyph); head.appendChild(name); head.appendChild(typeTag);
      li.appendChild(head);

      // editable value fields
      var fields = document.createElement("div");
      fields.className = "mark__fields";
      m.fields.forEach(function (f) {
        if (f.kind === "text") {
          var ta = document.createElement("textarea");
          ta.className = "fld__text"; ta.value = f.value; ta.rows = 2;
          ta.placeholder = "Type the constraint / note…";
          ta.addEventListener("click", stop);
          ta.addEventListener("input", function () { editLive(m.id, f.key, ta.value); });
          fields.appendChild(ta);
          return;
        }
        var row = document.createElement("label");
        row.className = "fld";
        var lab = document.createElement("span");
        lab.className = "fld__label"; lab.textContent = f.label;
        var inp = document.createElement("input");
        inp.type = "number"; inp.value = f.value; inp.className = "fld__input";
        inp.addEventListener("click", stop);
        inp.addEventListener("input", function () { editLive(m.id, f.key, parseFloat(inp.value || "0")); });
        var unit = document.createElement("span");
        unit.className = "fld__unit"; unit.textContent = f.unit;
        row.appendChild(lab); row.appendChild(inp); row.appendChild(unit);
        fields.appendChild(row);
      });
      li.appendChild(fields);

      // accept == apply to the real model (+ resolve); reject == discard
      var acts = document.createElement("div");
      acts.className = "mark__acts";
      var acceptLabel = m.tool === "note" ? "Accept" : "Accept (apply)";
      acts.appendChild(btn(acceptLabel, "act act--apply", function (ev) {
        stop(ev); send("accept", { id: m.id });
      }));
      acts.appendChild(btn("Reject", "act act--no", function (ev) {
        stop(ev); send("reject", { id: m.id });
      }));
      li.appendChild(acts);

      // comments
      var cwrap = document.createElement("div");
      cwrap.className = "mark__comments";
      (m.comments || []).forEach(function (c) {
        var cm = document.createElement("div");
        cm.className = "cmt"; cm.textContent = c.text;
        cwrap.appendChild(cm);
      });
      var crow = document.createElement("div");
      crow.className = "cmt__row";
      var cin = document.createElement("input");
      cin.type = "text"; cin.className = "cmt__input"; cin.placeholder = "Add a comment…";
      cin.addEventListener("click", stop);
      var post = btn("Post", "cmt__post", function (ev) {
        stop(ev);
        var t = cin.value.trim();
        if (t) { send("comment", { id: m.id, text: t }); cin.value = ""; }
      });
      cin.addEventListener("keydown", function (e) { if (e.key === "Enter") post.click(); });
      crow.appendChild(cin); crow.appendChild(post);
      cwrap.appendChild(crow);
      li.appendChild(cwrap);

      els.marks.appendChild(li);
    });
  }

  function btn(text, cls, fn) {
    var b = document.createElement("button");
    b.className = cls; b.textContent = text; b.addEventListener("click", fn);
    return b;
  }

  document.addEventListener("DOMContentLoaded", function () {
    cache(); render(); send("ready", {});
  });
})();
