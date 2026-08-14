/* FuzzyCAD sidebar — the async-collaboration panel (Overleaf-style).
 *
 * Three collaboration states are explicit in the UI:
 *   Need Input      geometry-changing questions that require a value/decision
 *   Constraint      geometry-linked notes/requirements
 *   Conflict        competing alternatives handled by Compare
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

  var GLYPH = {
    move: "⇄", rotate: "↻", scale: "⤢", scale_axis: "⇥",
    axis_rotate: "⟳", extrude: "⤒", fillet: "◜", note: "◈"
  };

  var MTYPES = [
    { key: "need_input", label: "Need Input", glyph: "!" },
    { key: "constraint", label: "Constraint", glyph: "‖" },
    { key: "conflict", label: "Conflict", glyph: "⑂" }
  ];

  function canonicalType(t) {
    // Old builds called the conflict state "alternative". Render old cards with
    // the new taxonomy without mutating their stored data.
    if (t === "alternative") return "conflict";
    if (t === "constraint" || t === "conflict") return t;
    return "need_input";
  }

  var editTimers = {};
  function editLive(id, key, value) {
    var k = id + ":" + key;
    if (editTimers[k]) clearTimeout(editTimers[k]);
    editTimers[k] = setTimeout(function () { send("edit", { id: id, key: key, value: value }); }, 120);
  }

  /* Move hover animation.
   * Keep the browser responsible only for timing. Fusion receives a throttled
   * normalized progress value and updates the CustomGraphics BRep on its main thread.
   */
  var hoverMoveTimer = null;
  var hoverMoveId = null;
  var hoverMoveStarted = 0;

  function easeMove(t) {
    return t * t * (3 - 2 * t);
  }

  function stopMoveHover(notifyFusion) {
    if (hoverMoveTimer) {
      clearInterval(hoverMoveTimer);
      hoverMoveTimer = null;
    }
    var oldId = hoverMoveId;
    hoverMoveId = null;
    hoverMoveStarted = 0;
    if (notifyFusion !== false && oldId !== null) {
      send("hoverMoveEnd", { id: oldId });
    }
  }

  function startMoveHover(mark) {
    if (!mark || mark.tool !== "move") return;
    if (hoverMoveId === mark.id && hoverMoveTimer) return;

    stopMoveHover(true);
    hoverMoveId = mark.id;
    hoverMoveStarted = performance.now();
    send("hoverMoveStart", { id: mark.id });

    function tick() {
      if (hoverMoveId !== mark.id) return;
      var elapsed = performance.now() - hoverMoveStarted;
      var cycle = elapsed % 1040;
      var t;
      if (cycle < 720) {
        t = easeMove(cycle / 720);
      } else if (cycle < 940) {
        t = 1.0;
      } else {
        t = 0.0;
      }
      send("hoverMoveFrame", { id: mark.id, t: t });
    }

    tick();
    hoverMoveTimer = setInterval(tick, 60);
  }

  function stop(ev) { ev.stopPropagation(); }

  function render() {
    stopMoveHover(true);

    els.count.textContent = state.marks.length + (state.marks.length === 1 ? " open question" : " open questions");
    els.empty.style.display = state.marks.length ? "none" : "block";
    els.marks.innerHTML = "";

    state.marks.forEach(function (m) {
      var mtype = canonicalType(m.mtype);
      var li = document.createElement("li");
      li.className = "mark mark--" + m.status + " type--" + mtype;
      li.title = m.tool === "move"
        ? "Hover to replay the proposed move; click to focus this in the model"
        : "Click to focus this in the model";
      li.addEventListener("click", function () { send("focus", { id: m.id }); });
      if (m.tool === "move") {
        li.addEventListener("mouseenter", function () { startMoveHover(m); });
        li.addEventListener("mouseleave", function () {
          if (hoverMoveId === m.id) stopMoveHover(true);
        });
      }

      var head = document.createElement("div");
      head.className = "mark__head";
      var glyph = document.createElement("span");
      glyph.className = "mark__glyph";
      glyph.textContent = GLYPH[m.tool] || "◆";
      var name = document.createElement("span");
      name.className = "mark__label";
      name.textContent = m.label || m.title;
      var mt = MTYPES.filter(function (t) { return t.key === mtype; })[0] || MTYPES[0];
      var typeTag = document.createElement("span");
      typeTag.className = "typetag typetag--" + mtype;
      typeTag.textContent = mt.glyph + " " + mt.label;
      head.appendChild(glyph);
      head.appendChild(name);
      head.appendChild(typeTag);
      li.appendChild(head);

      var fields = document.createElement("div");
      fields.className = "mark__fields";
      (m.fields || []).forEach(function (f) {
        if (f.kind === "text") {
          var ta = document.createElement("textarea");
          ta.className = "fld__text";
          ta.value = f.value;
          ta.rows = 2;
          ta.placeholder = "Type the constraint / note…";
          ta.addEventListener("click", stop);
          ta.addEventListener("input", function () { editLive(m.id, f.key, ta.value); });
          fields.appendChild(ta);
          return;
        }
        var row = document.createElement("label");
        row.className = "fld";
        var lab = document.createElement("span");
        lab.className = "fld__label";
        lab.textContent = f.label;
        var inp = document.createElement("input");
        inp.type = "number";
        inp.value = f.value;
        inp.className = "fld__input";
        inp.addEventListener("click", stop);
        inp.addEventListener("input", function () {
          editLive(m.id, f.key, parseFloat(inp.value || "0"));
        });
        var unit = document.createElement("span");
        unit.className = "fld__unit";
        unit.textContent = f.unit;
        row.appendChild(lab);
        row.appendChild(inp);
        row.appendChild(unit);
        fields.appendChild(row);
      });
      li.appendChild(fields);

      var acts = document.createElement("div");
      acts.className = "mark__acts";
      var acceptLabel = m.tool === "note" ? "Accept" : "Accept (apply)";
      acts.appendChild(btn(acceptLabel, "act act--apply", function (ev) {
        stop(ev);
        stopMoveHover(true);
        send("accept", { id: m.id });
      }));
      acts.appendChild(btn("Reject", "act act--no", function (ev) {
        stop(ev);
        stopMoveHover(true);
        send("reject", { id: m.id });
      }));
      li.appendChild(acts);

      var cwrap = document.createElement("div");
      cwrap.className = "mark__comments";
      (m.comments || []).forEach(function (c) {
        var cm = document.createElement("div");
        cm.className = "cmt";
        cm.textContent = c.text;
        cwrap.appendChild(cm);
      });
      var crow = document.createElement("div");
      crow.className = "cmt__row";
      var cin = document.createElement("input");
      cin.type = "text";
      cin.className = "cmt__input";
      cin.placeholder = "Add a comment…";
      cin.addEventListener("click", stop);
      var post = btn("Post", "cmt__post", function (ev) {
        stop(ev);
        var t = cin.value.trim();
        if (t) {
          send("comment", { id: m.id, text: t });
          cin.value = "";
        }
      });
      cin.addEventListener("keydown", function (e) {
        if (e.key === "Enter") post.click();
      });
      crow.appendChild(cin);
      crow.appendChild(post);
      cwrap.appendChild(crow);
      li.appendChild(cwrap);

      els.marks.appendChild(li);
    });
  }

  function btn(text, cls, fn) {
    var b = document.createElement("button");
    b.className = cls;
    b.textContent = text;
    b.addEventListener("click", fn);
    return b;
  }

  document.addEventListener("DOMContentLoaded", function () {
    cache();
    render();
    send("ready", {});
  });
})();
