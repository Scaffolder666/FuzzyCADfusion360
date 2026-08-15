/* Ask Fusion for sidebar state during palette startup without disrupting hover.
 *
 * Palette HTML and Design/persistence hydration can become ready in either order,
 * so a few startup retries remain useful.  Do not request state every time the
 * palette gains focus: moving the pointer from the viewport into the panel can
 * focus the palette, trigger a full state render, and cancel the hover dwell just
 * before replay starts. Document activation is already handled on the Python side.
 */
(function () {
  "use strict";

  function requestState() {
    try {
      if (window.adsk && typeof window.adsk.fusionSendData === "function") {
        window.adsk.fusionSendData("request_state", "{}");
      }
    } catch (e) {
      // The next scheduled startup request will retry.
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    [120, 450, 1100, 2400].forEach(function (delay) {
      window.setTimeout(requestState, delay);
    });
  });
})();
