/* Extra card-hover replay for non-Move FuzzyCAD proposals.
 * A short dwell distinguishes intentional inspection from simply moving the
 * pointer across the panel. Once started, replay remains real-time.
 */
(function () {
  "use strict";

  var SUPPORTED = { rotate:true, scale:true, scale_axis:true, axis_rotate:true, extrude:true };
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
      var cycle = elapsed % 1180;
      var t;
      if (cycle < 780) t = ease(cycle / 780);
      else if (cycle < 1040) t = 1.0;
      else t = 0.0;
      send("hoverOpFrame", { id: mark.id, tool: mark.tool, t: t });
    }

    tick();
    timer = setInterval(tick, 80);
  }

  function start(mark) {
    if (!mark || !SUPPORTED[mark.tool] || mark.reference_lost) return;
    if (hoverId === mark.id && (dwell || timer)) return;
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
      card.title = "Hover to replay the proposed " + mark.tool.replace("_", " ") + "; click to focus";
      card.addEventListener("mouseenter", function () { start(mark); });
      card.addEventListener("mouseleave", function () {
        if (hoverId === mark.id) stop(true);
      });
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
