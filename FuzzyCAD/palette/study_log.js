/* Lightweight study logger controls for the FuzzyCAD sidebar. */
(function () {
  "use strict";

  var active = false;
  var startedPerf = 0;
  var timer = null;

  function send(action, data) {
    if (window.adsk && typeof window.adsk.fusionSendData === "function") {
      return window.adsk.fusionSendData(action, JSON.stringify(data || {}));
    }
    return Promise.resolve(JSON.stringify({ ok: false, error: "Fusion bridge unavailable" }));
  }

  function parse(result) {
    try {
      return typeof result === "string" ? JSON.parse(result || "{}") : (result || {});
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  }

  function fmt(seconds) {
    var total = Math.max(0, Math.floor(Number(seconds) || 0));
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    function pad(n) { return String(n).padStart(2, "0"); }
    return h ? (h + ":" + pad(m) + ":" + pad(s)) : (pad(m) + ":" + pad(s));
  }

  function els() {
    return {
      button: document.getElementById("studyLogButton"),
      timer: document.getElementById("studyLogTimer"),
      status: document.getElementById("studyLogStatus")
    };
  }

  function stopTimer() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  function setActive(on, elapsed) {
    var e = els();
    active = !!on;
    stopTimer();

    if (!e.button || !e.timer || !e.status) return;

    if (active) {
      startedPerf = performance.now() - (Number(elapsed) || 0) * 1000;
      e.button.textContent = "Stop Logging";
      e.button.classList.add("studylog__button--stop");
      e.status.textContent = "Logging";
      e.status.classList.add("studylog__status--on");

      function tick() {
        e.timer.textContent = fmt((performance.now() - startedPerf) / 1000);
      }
      tick();
      timer = setInterval(tick, 1000);
    } else {
      e.button.textContent = "Start Logging";
      e.button.classList.remove("studylog__button--stop");
      e.status.textContent = "Off";
      e.status.classList.remove("studylog__status--on");
    }
  }

  function showError(message) {
    var e = els();
    if (e.status) {
      e.status.textContent = message || "Log error";
      e.status.classList.remove("studylog__status--on");
    }
  }

  function wire() {
    var e = els();
    if (!e.button || e.button._wired) return;
    e.button._wired = true;

    e.button.addEventListener("click", function () {
      e.button.disabled = true;

      if (!active) {
        send("studyLogStart", {}).then(function (raw) {
          var result = parse(raw);
          if (result.ok) {
            setActive(true, result.elapsed_sec || 0);
          } else {
            showError(result.error || "Could not start log");
          }
        }).catch(function (err) {
          showError(String(err));
        }).finally(function () {
          e.button.disabled = false;
        });
        return;
      }

      send("studyLogStop", {}).then(function (raw) {
        var result = parse(raw);
        if (result.ok) {
          setActive(false, 0);
          e.timer.textContent = fmt(result.duration_sec || 0);
          e.status.textContent = "Saved · " + (result.filename || "JSON");
        } else {
          showError(result.error || "Could not export log");
        }
      }).catch(function (err) {
        showError(String(err));
      }).finally(function () {
        e.button.disabled = false;
      });
    });

    send("studyLogStatus", {}).then(function (raw) {
      var result = parse(raw);
      if (result.ok) {
        setActive(!!result.active, result.elapsed_sec || 0);
        if (!result.active && result.filename) {
          e.status.textContent = "Saved · " + result.filename;
        }
      }
    });
  }

  document.addEventListener("DOMContentLoaded", wire);
})();
