/* Resolved-decision archive -- an Overleaf-style trail of cards that have been
 * Accepted or Rejected. Python pushes the archive as its own "archive" message
 * (separate from "state"), so this renderer never touches the live card render or
 * the incremental-patch path. Read-only: title + resolution + comments only.
 */
(function () {
  "use strict";

  var previous = window.fusionJavaScriptHandler;

  // ---- one-time styles ----------------------------------------------------
  function ensureStyle() {
    if (document.getElementById("archiveStyle")) return;
    var css = document.createElement("style");
    css.id = "archiveStyle";
    css.textContent = [
      ".archive{border-top:1px solid #e6eaef;background:#fbfcfd;font:12px/1.4 -apple-system,'Segoe UI',Roboto,sans-serif;color:#5a6672}",
      ".archive[hidden]{display:none}",
      ".archive__head{display:flex;align-items:center;gap:7px;width:100%;border:none;background:none;cursor:pointer;padding:9px 13px;color:#5a6672;font-weight:800;letter-spacing:.3px;text-transform:uppercase;font-size:10px}",
      ".archive__caret{transition:transform .15s;color:#9aa4af}",
      ".archive.open .archive__caret{transform:rotate(90deg)}",
      ".archive__count{color:#9aa4af;font-weight:700}",
      ".archive__body{display:none;flex-direction:column;gap:7px;padding:0 13px 12px}",
      ".archive.open .archive__body{display:flex}",
      ".arow{background:#fff;border:1px solid #e6eaef;border-radius:8px;padding:8px 10px}",
      ".arow__top{display:flex;align-items:center;gap:7px}",
      ".arow__title{font-weight:700;color:#2b3440}",
      ".arow__sum{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#7a8590;font-size:11px}",
      ".arow__res{font-size:9px;font-weight:800;letter-spacing:.3px;padding:2px 6px;border-radius:5px;color:#fff;flex:none}",
      ".arow__res--accepted{background:#1c7a4a}",
      ".arow__res--rejected{background:#b43b2e}",
      ".arow__cmts{margin-top:6px;display:flex;flex-direction:column;gap:4px}",
      ".acmt{background:#f4f6f8;border-radius:6px;padding:5px 8px;font-size:11px;color:#4a5560}",
      ".arow__none{margin-top:5px;font-size:10px;color:#aab2bc;font-style:italic}"
    ].join("");
    document.head.appendChild(css);
  }

  // ---- container, created lazily just below the open-questions list -------
  function container() {
    var el = document.getElementById("archive");
    if (el) return el;
    ensureStyle();
    el = document.createElement("section");
    el.id = "archive";
    el.className = "archive";
    el.hidden = true;

    var head = document.createElement("button");
    head.type = "button";
    head.className = "archive__head";
    var caret = document.createElement("span");
    caret.className = "archive__caret";
    caret.textContent = "▸";
    var label = document.createElement("span");
    label.textContent = "Resolved";
    var count = document.createElement("span");
    count.className = "archive__count";
    count.id = "archiveCount";
    head.appendChild(caret);
    head.appendChild(label);
    head.appendChild(count);
    head.addEventListener("click", function () {
      el.classList.toggle("open");
    });

    var body = document.createElement("div");
    body.className = "archive__body";
    body.id = "archiveBody";

    el.appendChild(head);
    el.appendChild(body);

    // Place it after the open-questions list, before the sticky Study Log.
    var list = document.querySelector(".list");
    var studylog = document.querySelector(".studylog");
    if (studylog && studylog.parentNode) {
      studylog.parentNode.insertBefore(el, studylog);
    } else if (list && list.parentNode) {
      list.parentNode.insertBefore(el, list.nextSibling);
    } else {
      document.body.appendChild(el);
    }
    return el;
  }

  function shortDate(ts) {
    if (!ts) return "";
    try {
      return new Date(ts * 1000).toLocaleDateString(undefined, {
        month: "short", day: "numeric"
      });
    } catch (e) {
      return "";
    }
  }

  function render(rows) {
    var el = container();
    var body = document.getElementById("archiveBody");
    var count = document.getElementById("archiveCount");
    rows = rows || [];

    el.hidden = rows.length === 0;
    if (count) count.textContent = "(" + rows.length + ")";
    if (!body) return;
    body.innerHTML = "";

    // Newest first.
    for (var i = rows.length - 1; i >= 0; i -= 1) {
      var r = rows[i] || {};
      var row = document.createElement("div");
      row.className = "arow";

      var top = document.createElement("div");
      top.className = "arow__top";

      var title = document.createElement("span");
      title.className = "arow__title";
      title.textContent = r.title || (r.tool || "Decision");

      var sum = document.createElement("span");
      sum.className = "arow__sum";
      sum.textContent = r.summary || "";

      var res = document.createElement("span");
      var resolution = r.resolution === "rejected" ? "rejected" : "accepted";
      res.className = "arow__res arow__res--" + resolution;
      res.textContent = (resolution === "rejected" ? "Rejected" : "Accepted") +
        (r.ts ? " · " + shortDate(r.ts) : "");

      top.appendChild(title);
      top.appendChild(sum);
      top.appendChild(res);
      row.appendChild(top);

      var comments = r.comments || [];
      if (comments.length) {
        var cwrap = document.createElement("div");
        cwrap.className = "arow__cmts";
        comments.forEach(function (c) {
          var cm = document.createElement("div");
          cm.className = "acmt";
          cm.textContent = (c && c.text) ? c.text : "";
          cwrap.appendChild(cm);
        });
        row.appendChild(cwrap);
      } else {
        var none = document.createElement("div");
        none.className = "arow__none";
        none.textContent = "no comments";
        row.appendChild(none);
      }

      body.appendChild(row);
    }
  }

  window.fusionJavaScriptHandler = {
    handle: function (action, dataString) {
      if (action === "archive") {
        try {
          var data = dataString ? JSON.parse(dataString) : {};
          render(data.archive || []);
        } catch (e) { /* ignore malformed archive push */ }
        return JSON.stringify({ ok: true });
      }
      if (previous && typeof previous.handle === "function") {
        return previous.handle(action, dataString);
      }
      return JSON.stringify({ ok: false });
    }
  };
})();
