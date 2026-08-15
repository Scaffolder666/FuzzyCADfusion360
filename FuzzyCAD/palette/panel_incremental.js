/* Keep live manipulator updates from rebuilding the entire collaboration panel.
 *
 * Python still sends authoritative state snapshots. When the card structure has
 * not changed, patch only visible field values in place. Structural changes
 * (add/remove question, comment, Compare selection, lost-reference state, etc.)
 * fall through to palette.js and perform the normal full render.
 */
(function () {
  "use strict";

  var previous = window.fusionJavaScriptHandler;
  if (!previous || typeof previous.handle !== "function") return;

  var lastSignature = null;

  function structureSignature(marks) {
    return JSON.stringify((marks || []).map(function (m) {
      return {
        id: m.id,
        tool: m.tool,
        mtype: m.mtype,
        status: m.status,
        label: m.label || m.title || "",
        reference_lost: !!m.reference_lost,
        selected: m.tool === "compare" ? m.selected : null,
        alternatives: m.tool === "compare" ? (m.alternatives || []).map(function (a) {
          return [a.name || "", (a.thumb || []).length];
        }) : null,
        comments: (m.comments || []).map(function (c) { return c.text || ""; }),
        fields: (m.fields || []).map(function (f) { return [f.key, f.kind || "number", f.unit || ""]; })
      };
    }));
  }

  function patchFields(marks) {
    var cards = document.querySelectorAll("#marks > .mark");
    if (cards.length !== marks.length) return false;

    var count = document.getElementById("count");
    if (count) {
      count.textContent = marks.length + (marks.length === 1 ? " open question" : " open questions");
    }

    for (var i = 0; i < marks.length; i += 1) {
      var mark = marks[i];
      var card = cards[i];
      if (!card) return false;
      var inputs = card.querySelectorAll(".fld__input, .fld__text");
      var fields = mark.fields || [];
      if (inputs.length !== fields.length) return false;
      for (var j = 0; j < fields.length; j += 1) {
        var input = inputs[j];
        if (!input || document.activeElement === input) continue;
        var value = fields[j].value;
        var next = value === null || typeof value === "undefined" ? "" : String(value);
        if (input.value !== next) input.value = next;
      }
    }
    return true;
  }

  window.fusionJavaScriptHandler = {
    handle: function (action, dataString) {
      if (action !== "state") return previous.handle(action, dataString);
      try {
        var data = dataString ? JSON.parse(dataString) : {};
        var marks = data.marks || [];
        var signature = structureSignature(marks);
        if (lastSignature !== null && signature === lastSignature && patchFields(marks)) {
          return JSON.stringify({ ok: true, incremental: true });
        }
        var result = previous.handle(action, dataString);
        lastSignature = signature;
        return result;
      } catch (e) {
        return previous.handle(action, dataString);
      }
    }
  };
})();
