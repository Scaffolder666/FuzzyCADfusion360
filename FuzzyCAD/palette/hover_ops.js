/* Extra card-hover replay for non-Move FuzzyCAD proposals.
 * Hover is only a lightweight preview of change tendency: wait briefly, move
 * once toward the proposal, hold there while the pointer stays on the card, and
 * disappear immediately on leave/click. It never expands persistent geometry.
 */
(function () {
  "use strict";

  var SUPPORTED = { rotate:true, scale:true, scale_axis:true, axis_rotate:true, extrude:true };
  var FRAME_MS = 90;
  var FORWARD_MS = 900;
  var timer = null;
  var dwell = null;
  var hoverId = null;
  var hoverTool = null;
  var started = 0;
  var active = false;

  function send(action, data) {
    if (window.adsk && typeof window.adsk.fusionSendData === "function") {
      return window.adsk.fusionSendData(action, JSON.stringify(data || {}));
    }
    return Promise.resolve("{}");
  }

  function ease(t) { return t * t * (3 - 2 * t); }

  function stop(notifyFusion) {
    if (dwell) { clearTimeout(dwell); dwell = null; }
    if (timer) { clearInterval(timer); timer = null; }
    var oldId = hoverId;
    var wasActive = active;
    hoverId = null; hoverTool = null; started = 0; active = false;
    if (notifyFusion !== false && wasActive && oldId !== null) {
      send("hoverOpEnd", { id: oldId });
    }
  }

  function begin(mark) {
    if (!mark || hoverId !== mark.id) return;
    active = true;
    hoverTool = mark.tool;
    started = performance.now();
    send("hoverOpStart", { id: mark.id, tool: mark.tool });

    function tick() {
      if (hoverId !== mark.id || !active) return;
      var elapsed = performance.now() - started;
      if (elapsed >= FORWARD_MS) {
        send("hoverOpFrame", { id: mark.id, tool: mark.tool, t: 1.0 });
        if (timer) { clearInterval(timer); timer = null; }
        return;
      }
      send("hoverOpFrame", {
        id: mark.id,
        tool: mark.tool,
        t: ease(elapsed / FORWARD_MS)
      });
    }

    tick();
    timer = setInterval(tick, FRAME_MS);
  }

  function start(mark) {
    if (!mark || !SUPPORTED[mark.tool] || mark.reference_lost) return;
    if (hoverId === mark.id && (dwell || timer || active)) return;
    stop(true);
    hoverId = mark.id;
    hoverTool = mark.tool;
    dwell = setTimeout(function () {
      dwell = null;
      begin(mark);
    }, 220);
  }

  function attach(marks) {
    var cards = document.querySelectorAll("#marks > .mark");
    Array.prototype.forEach.call(cards, function (card, i) {
      var mark = marks[i];
      if (!mark || !SUPPORTED[mark.tool] || mark.reference_lost) return;
      /* Incremental state updates preserve DOM nodes. Never stack duplicate
       * listeners when the same cards receive another state snapshot. */
      if (card.getAttribute("data-hover-op-bound") === "1") return;
      card.setAttribute("data-hover-op-bound", "1");
      card.title = "Hover to preview the proposed " + mark.tool.replace("_", " ") + "; click to inspect/edit";
      card.addEventListener("mouseenter", function () { start(mark); });
      card.addEventListener("mouseleave", function () {
        if (hoverId === mark.id) stop(true);
      });
      /* Stop before the card's normal click handler focuses/opens the proposal,
       * so replay graphics can never overlap the inspected geometry. */
      card.addEventListener("click", function () {
        if (hoverId === mark.id) stop(true);
      }, true);
    });
  }

  var oldHandler = window.fusionJavaScriptHandler;
  if (oldHandler && typeof oldHandler.handle === "function") {
    window.fusionJavaScriptHandler = {
      handle: function (action, dataString) {
        if (action === "state") stop(true);
        var result = oldHandler.handle(action, dataString);
        if (action === "state") {
          try {
            var data = dataString ? JSON.parse(dataString) : {};
            attach(data.marks || []);
          } catch (e) {}
        }
        return result;
      }
    };
  }

  window.addEventListener("beforeunload", function () { stop(false); });
})();
