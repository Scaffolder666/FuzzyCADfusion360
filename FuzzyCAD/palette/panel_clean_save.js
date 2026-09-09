(function () {
  "use strict";

  var button = document.getElementById("cleanSave");
  if (!button) return;

  function send(action, data) {
    if (window.adsk && typeof window.adsk.fusionSendData === "function") {
      return window.adsk.fusionSendData(action, JSON.stringify(data || {}));
    }
    return Promise.reject(new Error("Fusion bridge unavailable"));
  }

  button.addEventListener("click", function () {
    if (button.disabled) return;
    button.disabled = true;
    var old = button.textContent;
    button.textContent = "Saving…";

    try {
      var result = send("cleanSave", {});
      if (result && typeof result.catch === "function") {
        result.catch(function () {
          button.disabled = false;
          button.textContent = old;
        });
      }
    } catch (e) {
      button.disabled = false;
      button.textContent = old;
    }

    // On success Fusion closes/reopens the design, replacing this palette view.
    // If the action returns without a reload (for example because the document
    // is unsaved or an edit command is active), restore the button automatically.
    setTimeout(function () {
      if (document.body && button.isConnected) {
        button.disabled = false;
        button.textContent = old;
      }
    }, 4500);
  });
})();
