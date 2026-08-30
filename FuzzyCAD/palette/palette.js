/* FuzzyCAD sidebar — the async-collaboration panel (Overleaf-style).
 *
 * Three collaboration states are explicit in the UI:
 *   Need Input      geometry-changing questions that require a value/decision
 *   Constraint      geometry-linked notes/requirements
 *   Conflict        competing alternatives handled by Compare
 */
(function () {
  "use strict";

  var state = { marks: [] };
  var ANIMATION_DWELL_MS = 650;
  window.FuzzyCADAnimationDwellMs = ANIMATION_DWELL_MS;

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
    els.dumpState = document.getElementById("dumpState");
    if (els.dumpState && !els.dumpState._wired) {
      els.dumpState._wired = true;
      els.dumpState.addEventListener("click", function () { send("dumpState", {}); });
    }
    els.clearAll = document.getElementById("clearAll");
    if (els.clearAll && !els.clearAll._wired) {
      els.clearAll._wired = true;
      // Two-click confirm — deleting every stored question is irreversible and
      // there is no browser confirm() dialog in the Fusion palette webview.
      var armed = false, timer = null;
      els.clearAll.addEventListener("click", function () {
        if (!armed) {
          armed = true;
          els.clearAll.textContent = "Delete all?";
          timer = setTimeout(function () {
            armed = false; els.clearAll.textContent = "Clear all";
          }, 3500);
          return;
        }
        if (timer) clearTimeout(timer);
        armed = false;
        els.clearAll.textContent = "Clear all";
        send("clearAll", {});
      });
    }
  }

  var GLYPH = {
    move: "⇄", rotate: "↻", scale: "⤢", scale_axis: "⇥",
    axis_rotate: "⟳", extrude: "⤒", fillet: "◜", hole: "⊙",
    rough: "▱", note: "◈", compare: "⑂"
  };

  var MTYPES = [
    { key: "need_input", label: "Need Input", glyph: "!" },
    { key: "constraint", label: "Constraint", glyph: "‖" },
    { key: "conflict", label: "Conflict", glyph: "⑂" }
  ];

  function canonicalType(t) {
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

  /* Move replay remains real-time after intent is clear, but a deliberate dwell
   * keeps casual pointer travel and aiming for card controls from opening a
   * JS -> Python -> viewport refresh loop. */
  var hoverMoveTimer = null;
  var hoverMoveDwell = null;
  var hoverMoveId = null;
  var hoverMoveStarted = 0;
  var hoverMoveActive = false;

  function easeMove(t) { return t * t * (3 - 2 * t); }

  function stopMoveHover(notifyFusion) {
    if (hoverMoveDwell) { clearTimeout(hoverMoveDwell); hoverMoveDwell = null; }
    if (hoverMoveTimer) { clearInterval(hoverMoveTimer); hoverMoveTimer = null; }
    var oldId = hoverMoveId;
    var wasActive = hoverMoveActive;
    hoverMoveId = null;
    hoverMoveStarted = 0;
    hoverMoveActive = false;
    if (notifyFusion !== false && wasActive && oldId !== null) {
      send("hoverMoveEnd", { id: oldId });
    }
  }

  function beginMoveHover(mark) {
    if (!mark || hoverMoveId !== mark.id) return;
    hoverMoveActive = true;
    hoverMoveStarted = performance.now();
    send("hoverMoveStart", { id: mark.id });
    function tick() {
      if (hoverMoveId !== mark.id || !hoverMoveActive) return;
      var elapsed = performance.now() - hoverMoveStarted;
      var cycle = elapsed % 1040;
      var t;
      if (cycle < 720) t = easeMove(cycle / 720);
      else if (cycle < 940) t = 1.0;
      else t = 0.0;
      send("hoverMoveFrame", { id: mark.id, t: t });
    }
    tick();
    hoverMoveTimer = setInterval(tick, 60);
  }

  function startMoveHover(mark) {
    if (!mark || mark.tool !== "move" || mark.reference_lost) return;
    if (hoverMoveId === mark.id && (hoverMoveDwell || hoverMoveTimer)) return;
    stopMoveHover(true);
    hoverMoveId = mark.id;
    hoverMoveDwell = setTimeout(function () {
      hoverMoveDwell = null;
      beginMoveHover(mark);
    }, ANIMATION_DWELL_MS);
  }

  function stop(ev) { ev.stopPropagation(); }

  function svgThumb(lines) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 100 70");
    svg.setAttribute("class", "alt__svg");
    (lines || []).forEach(function (poly) {
      if (!poly || poly.length < 2) return;
      var pl = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      pl.setAttribute("points", poly.map(function (p) { return p[0] + "," + p[1]; }).join(" "));
      pl.setAttribute("fill", "none");
      pl.setAttribute("vector-effect", "non-scaling-stroke");
      svg.appendChild(pl);
    });
    return svg;
  }

  function compareOptions(m) {
    var wrap = document.createElement("div");
    wrap.className = "compare";

    var hint = document.createElement("div");
    hint.className = "compare__hint";
    hint.textContent = m.selected === 0 || m.selected === 1
      ? "Previewing the selected alternative at " + (m.target_label || "the target")
      : "Unresolved — choose one of the alternatives below";
    wrap.appendChild(hint);

    var grid = document.createElement("div");
    grid.className = "compare__grid";
    (m.alternatives || []).slice(0, 2).forEach(function (alt, idx) {
      var card = document.createElement("button");
      card.type = "button";
      card.className = "alt" + (m.selected === idx ? " alt--selected" : "");
      card.disabled = !!m.reference_lost;
      card.addEventListener("click", function (ev) {
        stop(ev);
        if (!m.reference_lost) send("compare_choice", { id: m.id, choice: idx });
      });
      var pic = document.createElement("div");
      pic.className = "alt__pic";
      pic.appendChild(svgThumb(alt.thumb || []));
      var label = document.createElement("div");
      label.className = "alt__label";
      label.textContent = alt.name || ("Alternative " + (idx + 1));
      var badge = document.createElement("div");
      badge.className = "alt__badge";
      badge.textContent = "Alternative " + (idx + 1);
      card.appendChild(pic); card.appendChild(label); card.appendChild(badge);
      grid.appendChild(card);
    });
    wrap.appendChild(grid);
    return wrap;
  }

  function referenceWarning(m) {
    if (!m.reference_lost) return null;
    var box = document.createElement("div");
    box.className = "refwarn";
    var title = document.createElement("div");
    title.className = "refwarn__title";
    title.textContent = "Geometry changed";
    var text = document.createElement("div");
    text.className = "refwarn__text";
    text.textContent = m.can_relink
      ? "This question is no longer linked to its original geometry."
      : "This comparison lost an assembly reference. Recreate Compare to restore all connectors.";
    box.appendChild(title);
    box.appendChild(text);
    if (m.can_relink) {
      var relink = btn("Relink geometry", "refwarn__action", function (ev) {
        stop(ev);
        send("relink", { id: m.id });
      });
      box.appendChild(relink);
    }
    return box;
  }

  function render() {
    stopMoveHover(true);

    els.count.textContent = state.marks.length + (state.marks.length === 1 ? " open question" : " open questions");
    els.empty.style.display = state.marks.length ? "none" : "block";
    if (els.clearAll) els.clearAll.style.display = state.marks.length ? "inline-block" : "none";
    els.marks.innerHTML = "";

    state.marks.forEach(function (m) {
      var mtype = canonicalType(m.mtype);
      var li = document.createElement("li");
      li.className = "mark mark--" + m.status + " type--" + mtype + (m.reference_lost ? " mark--reference-lost" : "");
      if (m.reference_lost) {
        li.title = "This question needs to be relinked to geometry";
      } else if (mtype === "need_input") {
        li.title = m.tool === "move"
          ? "Pause to replay; click to reopen the viewport manipulator"
          : "Click to reopen the viewport manipulator";
      } else {
        li.title = "Click to focus this in the model";
      }
      li.addEventListener("click", function () {
        if (m.reference_lost) {
          send("focus", { id: m.id });
        } else if (mtype === "need_input") {
          stopMoveHover(true);
          send("editManipulator", { id: m.id });
        } else {
          send("focus", { id: m.id });
        }
      });
      if (m.tool === "move" && !m.reference_lost) {
        li.addEventListener("mouseenter", function () { startMoveHover(m); });
        li.addEventListener("mousemove", function (ev) {
          if (hoverMoveId !== m.id || !ev.target || typeof ev.target.closest !== "function") return;
          if (ev.target.closest("button,input,textarea,select,a,[contenteditable='true']")) {
            stopMoveHover(true);
          }
        });
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
      head.appendChild(glyph); head.appendChild(name); head.appendChild(typeTag);
      li.appendChild(head);

      var warning = referenceWarning(m);
      if (warning) li.appendChild(warning);

      if (m.tool === "compare") {
        li.appendChild(compareOptions(m));
      } else {
        var fields = document.createElement("div");
        fields.className = "mark__fields";
        (m.fields || []).forEach(function (f) {
          if (f.kind === "text") {
            var ta = document.createElement("textarea");
            ta.className = "fld__text";
            ta.value = f.value;
            ta.rows = 2;
            ta.placeholder = "Type the constraint / note…";
            ta.disabled = !!m.reference_lost;
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
          inp.disabled = !!m.reference_lost;
          inp.addEventListener("click", stop);
          inp.addEventListener("input", function () {
            editLive(m.id, f.key, parseFloat(inp.value || "0"));
          });
          var unit = document.createElement("span");
          unit.className = "fld__unit";
          unit.textContent = f.unit;
          row.appendChild(lab); row.appendChild(inp); row.appendChild(unit);
          fields.appendChild(row);
        });
        li.appendChild(fields);
      }

      var acts = document.createElement("div");
      acts.className = "mark__acts";
      var acceptLabel = (m.tool === "note" || m.tool === "rough") ? "Accept" : (m.tool === "compare" ? "Confirm choice" : "Accept (apply)");
      var apply = btn(acceptLabel, "act act--apply", function (ev) {
        stop(ev);
        stopMoveHover(true);
        if (!m.reference_lost) send("accept", { id: m.id });
      });
      if (m.reference_lost) {
        apply.disabled = true;
        apply.title = "Relink this question before applying it";
      } else if (m.tool === "compare" && !(m.selected === 0 || m.selected === 1)) {
        apply.disabled = true;
        apply.title = "Choose an alternative first";
      }
      acts.appendChild(apply);
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
      crow.appendChild(cin); crow.appendChild(post); cwrap.appendChild(crow);

      // Reference images — part of the comment layer, on any mark.
      var imgrow = document.createElement("div");
      imgrow.style.cssText = "display:flex;gap:6px;align-items:center;margin-top:6px;flex-wrap:wrap";
      var imgBtnCss = "border:1px solid #d3d8df;background:#fff;color:#5a6672;font:600 10.5px/1 -apple-system,'Segoe UI',Roboto,sans-serif;padding:5px 8px;border-radius:6px;cursor:pointer";
      var bFace = btn("📎 Image on face", "img__btn", function (ev) { stop(ev); send("attachImageFace", { id: m.id }); });
      var bNode = btn("📎 Floating image", "img__btn", function (ev) { stop(ev); send("attachImageNode", { id: m.id }); });
      bFace.style.cssText = imgBtnCss; bNode.style.cssText = imgBtnCss;
      imgrow.appendChild(bFace); imgrow.appendChild(bNode);
      cwrap.appendChild(imgrow);

      // Thumbnail + show/hide toggle for every attached image (floating Canvas
      // near the object, or Canvas on a face). Toggle flips the Canvas visibility.
      var allImgs = m.images || [];
      if (allImgs.length) {
        var thumbrow = document.createElement("div");
        thumbrow.style.cssText = "display:flex;gap:8px;align-items:center;margin-top:7px;flex-wrap:wrap";
        allImgs.forEach(function (im) {
          var chip = document.createElement("div");
          chip.style.cssText = "display:flex;align-items:center;gap:5px;border:1px solid #d3d8df;border-radius:7px;padding:3px 5px;background:#fff";
          if (im.thumb_uri) {
            var thumb = document.createElement("img");
            thumb.src = im.thumb_uri;
            thumb.style.cssText = "width:34px;height:34px;object-fit:cover;border-radius:4px;opacity:" + (im.hidden ? "0.3" : "1");
            chip.appendChild(thumb);
          }
          var lbl = document.createElement("span");
          lbl.style.cssText = "font:600 10px/1 -apple-system,sans-serif;color:#8a94a0";
          lbl.textContent = im.floating ? "by object" : (im.mode === "face" ? "on face" : "image");
          chip.appendChild(lbl);
          var tgl = btn(im.hidden ? "Show" : "Hide", "img__tgl", (function (index) {
            return function (ev) { stop(ev); send("toggleImageNode", { id: m.id, index: index }); };
          })(im.index));
          tgl.title = im.hidden ? "Show in viewport" : "Hide in viewport";
          tgl.style.cssText = "border:1px solid #d3d8df;background:" + (im.hidden ? "#eef2f6" : "#fff") + ";color:#5a6672;font:600 10px/1 -apple-system,sans-serif;padding:4px 7px;border-radius:6px;cursor:pointer";
          chip.appendChild(tgl);
          thumbrow.appendChild(chip);
        });
        cwrap.appendChild(thumbrow);
      }

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
    cache(); render(); send("ready", {});
  });
})();