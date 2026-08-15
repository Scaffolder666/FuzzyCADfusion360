/* Ask Fusion for sidebar state more than once during palette startup.
 *
 * Palette HTML and Design/persistence hydration do not always become ready in the
 * same order. These requests are intentionally idempotent; Python only reloads
 * persisted state when its runtime mark list is still empty.
 */
(function () {
  "use strict";

  function requestState() {
    try {
      if (window.adsk && typeof window.adsk.fusionSendData === "function") {
        window.adsk.fusionSendData("request_state", "{}");
      }
    } catch (e) {
      // The next scheduled request will retry.
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    [120, 450, 1100, 2400].forEach(function (delay) {
      window.setTimeout(requestState, delay);
    });
  });

  window.addEventListener("focus", requestState);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) requestState();
  });
})();
