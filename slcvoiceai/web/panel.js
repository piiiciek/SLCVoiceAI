/* The page half of the panel.
 *
 * This file holds no wording and no decisions. Every visible string arrives
 * from gui.py already translated, and every question worth an opinion -
 * what an entry means, whether a score passes - was answered in Python
 * before it got here. What is left is putting things on screen.
 *
 * Two rules worth keeping:
 *
 *   1. Text from the bridge goes in with textContent, never innerHTML.
 *      Transcriptions are whatever the microphone heard and button names
 *      are whatever SLC calls them; neither is trusted markup.
 *
 *   2. The feed is never re-rendered in a new language. Those lines are a
 *      record of what happened, and rewriting history would be a strange
 *      thing for a log to do.
 */

(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var phrases = {};
  var api = null;

  /* ---------- wording ------------------------------------------------- */

  function say(key) {
    return Object.prototype.hasOwnProperty.call(phrases, key) ? phrases[key] : key;
  }

  function applyPhrases(table) {
    phrases = table || {};
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      el.textContent = say(el.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      el.placeholder = say(el.dataset.i18nPlaceholder);
    });
    document.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      el.title = say(el.dataset.i18nTitle);
    });
    // The hotkey rows are built in script, so they carry no data-i18n for
    // the sweep above to find - their wording has to be drawn again.
    render();
  }

  /* ---------- the surface gui.py drives ------------------------------- */

  var PLAY = "M8 5v14l11-7z";
  var STOP = "M6 6h12v12H6z";

  var panel = {

    phrases: applyPhrases,

    /** state is one of stopped / loading / listening / failed. */
    status: function (text, state) {
      var card = $("status-card");
      card.className = "card is-" + state;
      $("status-text").textContent = text;
      $("toggle-glyph").firstElementChild.setAttribute(
        "d", state === "listening" ? STOP : PLAY);
    },

    subtitle: function (text) {
      $("subtitle").textContent = text || "";
    },

    button: function (text, enabled) {
      $("toggle-text").textContent = text;
      $("toggle").disabled = !enabled;
    },

    /** entry is {time, text, tag}. */
    feed: function (entry) {
      var feed = $("feed");
      // Whether to follow the tail is decided before appending, so that
      // reading back through a flight is not yanked to the bottom by the
      // next line to arrive.
      var following = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 24;

      var row = document.createElement("div");
      row.className = "entry " + (entry.tag || "");

      var time = document.createElement("span");
      time.className = "time";
      time.textContent = entry.time;

      var message = document.createElement("span");
      message.className = "msg";
      message.textContent = entry.text;

      row.appendChild(time);
      row.appendChild(message);
      feed.appendChild(row);

      // Keep the buffer from growing without bound over a long flight.
      while (feed.childElementCount > 400) {
        feed.removeChild(feed.firstElementChild);
      }
      if (following) feed.scrollTop = feed.scrollHeight;
    },

    feedMany: function (entries) {
      (entries || []).forEach(panel.feed);
    },

    update: function (text) {
      $("update-text").textContent = text;
      $("update").hidden = false;
    },

    /** rows is [{action, label, combo, shown}], all three, bound or not. */
    hotkeys: function (rows) {
      drawHotkeys(rows || []);
    },

    controls: function (state) {
      $("version").textContent = state.version;
      $("dry").checked = !!state.dry;
      $("conf").value = state.confidence;
      $("conf-out").textContent = Number(state.confidence).toFixed(2);

      var picker = $("lang");
      picker.textContent = "";
      (state.languages || []).forEach(function (pair) {
        var option = document.createElement("option");
        option.value = pair[0];
        option.textContent = pair[1];
        picker.appendChild(option);
      });
      picker.value = state.language;
    }
  };

  window.panel = panel;

  /* ---------- talking back -------------------------------------------- */

  // Nothing here may throw into pywebview's bridge: a rejected call is
  // invisible to the pilot and leaves the panel looking wedged.
  function ask(method) {
    var args = Array.prototype.slice.call(arguments, 1);
    if (!api || typeof api[method] !== "function") return;
    try {
      var result = api[method].apply(api, args);
      if (result && typeof result.catch === "function") {
        result.catch(function (err) { console.error(method, err); });
      }
    } catch (err) {
      console.error(method, err);
    }
  }

  $("toggle").addEventListener("click", function () {
    // Python decides what the button becomes; disabling it here only stops
    // a second click landing while that decision is in flight.
    $("toggle").disabled = true;
    ask("toggle");
  });

  $("dry").addEventListener("change", function (event) {
    ask("set_dry", event.target.checked);
  });

  // The readout follows the thumb; the bridge is told when it is let go.
  // The floor is only consulted when an utterance arrives, so there is
  // nothing to gain from a round trip per pixel.
  $("conf").addEventListener("input", function (event) {
    $("conf-out").textContent = Number(event.target.value).toFixed(2);
  });
  $("conf").addEventListener("change", function (event) {
    ask("set_threshold", Number(event.target.value));
  });

  $("lang").addEventListener("change", function (event) {
    ask("set_language", event.target.value);
  });

  function tryPhrase() {
    var box = $("phrase");
    var said = box.value.trim();
    if (!said) return;
    box.value = "";
    ask("try_phrase", said);
  }

  $("match").addEventListener("click", tryPhrase);
  $("phrase").addEventListener("keydown", function (event) {
    if (event.key === "Enter") tryPhrase();
  });

  $("clear").addEventListener("click", function () {
    $("feed").textContent = "";
  });

  $("update").addEventListener("click", function () {
    ask("open_repository");
  });

  /* ---------- hotkeys ---------------------------------------------------
     The page holds a working copy while it is being edited, then sends the
     whole list. Python answers with what it accepted and the page redraws
     from that, so a refused binding cannot linger on screen looking saved. */

  var rows = [];
  var listening = null;          // the row waiting for a combination, if any

  function drawHotkeys(fresh) {
    rows = fresh || [];
    listening = null;
    render();
  }

  function render() {
    var host = $("hotkeys");
    if (!host) return;
    host.textContent = "";

    rows.forEach(function (row) {
      var line = document.createElement("div");
      line.className = "hotkey";

      var label = document.createElement("span");
      label.className = "what";
      label.textContent = row.label;

      var cap = document.createElement("button");
      cap.className = "keycap" + (row.shown ? "" : " unbound");
      cap.textContent = row.shown || say("hotkeys.unbound");
      cap.addEventListener("click", function () { listenOn(cap, row.action); });

      var clear = document.createElement("button");
      clear.className = "icon-btn";
      clear.textContent = "×";
      clear.title = say("hotkeys.clear");
      clear.disabled = !row.shown;
      clear.addEventListener("click", function () {
        if (!api) return;
        api.clear_binding(row.action).then(answered)
           .catch(function (err) { console.error("clear_binding", err); });
      });

      line.appendChild(label);
      line.appendChild(cap);
      line.appendChild(clear);
      host.appendChild(line);
    });
  }

  function answered(answer) {
    if (answer && answer.rows) drawHotkeys(answer.rows);
  }

  function listenOn(cap, action) {
    if (listening) render();       // drop whichever row was listening before
    listening = { cap: cap, action: action };
    cap.classList.add("listening");
    cap.textContent = say("hotkeys.press");
    cap.focus();
  }

  // Captured on the window so it works wherever focus happens to be, and
  // swallowed so binding Tab or Space does not also move focus or scroll.
  window.addEventListener("keydown", function (event) {
    if (!listening) return;
    event.preventDefault();
    event.stopPropagation();

    // Ctrl on its own is the start of a combination, not a binding. Keep
    // waiting rather than refusing something nobody meant to press.
    if (event.key === "Control" || event.key === "Alt" ||
        event.key === "Shift" || event.key === "Meta") {
      return;
    }

    var at = listening;
    listening = null;
    if (event.code === "Escape") {          // changed your mind
      render();
      return;
    }
    if (!api) { render(); return; }

    api.set_binding(at.action, {
      code: event.code,
      ctrl: event.ctrlKey,
      alt: event.altKey,
      shift: event.shiftKey,
      meta: event.metaKey
    }).then(answered).catch(function (err) {
      console.error("set_binding", err);
      render();
    });
  }, true);

  /* ---------- start ---------------------------------------------------- */

  window.addEventListener("pywebviewready", function () {
    api = window.pywebview.api;
    // One call for the whole opening state, so the page is never briefly
    // shown with English labels or a stale threshold.
    api.boot().then(function (state) {
      applyPhrases(state.phrases);
      panel.controls(state);
      panel.status(state.status.text, state.status.state);
      panel.button(state.button, true);
      panel.subtitle(state.subtitle);
      panel.hotkeys(state.hotkeys);
      panel.feedMany(state.entries);
    }).catch(function (err) {
      console.error("boot", err);
    });
  });
}());
