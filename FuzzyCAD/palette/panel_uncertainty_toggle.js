/* Global show/hide toggle for all uncertainty overlays.
 * Sends toggleUncertainty to Python; reflects the returned hidden state on the
 * button. Display-only -- nothing is deleted. */
(function () {
  "use strict";

  function send(action, data) {
    try {
      if (window.adsk && typeof window.adsk.fusionSendData === "function") {
        return window.adsk.fusionSendData(action, JSON.stringify(data || {}));
      }
    } catch (e) { /* ignore */ }
    return Promise.resolve("{}");
  }

  function parse(raw) {
    try { return typeof raw === "string" ? JSON.parse(raw || "{}") : (raw || {}); }
    catch (e) { return {}; }
  }

  function paint(btn, hidden) {
    // hidden === true  -> overlays are hidden, button offers "Show"
    // hidden === false -> overlays are shown,  button offers "Hide"
    btn.textContent = hidden ? "👁 Show" : "👁 Hide";
    btn.style.background = hidden ? "#eef2f6" : "#fff";
    btn.style.color = hidden ? "#2b3440" : "#5a6672";
  }

  function wire() {
    var btn = document.getElementById("toggleUncertainty");
    if (!btn || btn._wired) return;
    btn._wired = true;

    btn.addEventListener("click", function () {
      Promise.resolve(send("toggleUncertainty", {})).then(function (raw) {
        var r = parse(raw);
        if (r && typeof r.hidden === "boolean") paint(btn, r.hidden);
      });
    });

    // Reflect the current state on load (in case the panel reloaded while hidden).
    Promise.resolve(send("uncertaintyStatus", {})).then(function (raw) {
      var r = parse(raw);
      if (r && typeof r.hidden === "boolean") paint(btn, r.hidden);
    });
  }

  document.addEventListener("DOMContentLoaded", wire);
})();
