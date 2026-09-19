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

    /** rows is [{key, buttons}], exactly as gui.py accepted them. */
    hotkeys: function (rows) {
        drawHotkeys(rows || []);
    },

    /** Button names read out of SLC, offered as you type. */
    suggestions: function (names) {
      var list = $("slc-buttons");
      list.textContent = "";
      (names || []).forEach(function (name) {
        var option = document.createElement("option");
        option.value = name;
        list.appendChild(option);
      });
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
  var listening = null;          // the keycap waiting for a key, if any

  function drawHotkeys(fresh) {
    rows = fresh.map(function (row) {
      return { key: row.key, buttons: row.buttons };
    });
    listening = null;
    render();
  }

  function render() {
    var host = $("hotkeys");
    host.textContent = "";

    if (!rows.length) {
      var empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = say("hotkeys.none");
      host.appendChild(empty);
      return;
    }

    rows.forEach(function (row, index) {
      var line = document.createElement("div");
      line.className = "hotkey";

      var cap = document.createElement("button");
      cap.className = "keycap";
      cap.textContent = row.key || say("hotkeys.press");
      cap.addEventListener("click", function () { listenOn(cap, index); });

      var arrow = document.createElement("span");
      arrow.className = "arrow";
      arrow.textContent = "→";

      var box = document.createElement("input");
      box.className = "text-input";
      box.value = row.buttons;
      box.placeholder = say("hotkeys.button");
      box.setAttribute("list", "slc-buttons");
      box.autocomplete = "off";
      box.spellcheck = false;
      box.addEventListener("input", function () { rows[index].buttons = box.value; });
      box.addEventListener("change", commit);

      var bin = document.createElement("button");
      bin.className = "icon-btn";
      bin.textContent = "×";
      bin.title = say("hotkeys.remove");
      bin.addEventListener("click", function () {
        rows.splice(index, 1);
        render();
        commit();
      });

      line.appendChild(cap);
      line.appendChild(arrow);
      line.appendChild(box);
      line.appendChild(bin);
      host.appendChild(line);
    });
  }

  function listenOn(cap, index) {
    if (listening) listening.cap.classList.remove("listening");
    listening = { cap: cap, index: index };
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

    var at = listening;
    if (event.code === "Escape") {          // changed your mind
      at.cap.classList.remove("listening");
      at.cap.textContent = rows[at.index].key || say("hotkeys.press");
      listening = null;
      return;
    }

    if (!api) return;
    api.capture_key(event.code).then(function (answer) {
      at.cap.classList.remove("listening");
      listening = null;
      if (answer && answer.key) {
        rows[at.index].key = answer.key;
        commit();
      }
      at.cap.textContent = rows[at.index] ? (rows[at.index].key
                                             || say("hotkeys.press")) : "";
      render();
    }).catch(function (err) { console.error("capture_key", err); });
  }, true);

  function commit() {
    if (!api) return;
    // A row being filled in - Add pressed, key chosen, button name not
    // typed yet - is not something Python stores, so it does not come back
    // in the answer. Carrying it over is what stops a new binding
    // vanishing from under the pilot halfway through making it.
    var pending = rows.filter(function (row) { return !row.key || !row.buttons; });
    api.save_hotkeys(rows).then(function (answer) {
      if (answer && answer.rows) drawHotkeys(answer.rows.concat(pending));
    }).catch(function (err) { console.error("save_hotkeys", err); });
  }

  $("add-hotkey").addEventListener("click", function () {
    rows.push({ key: "", buttons: "" });
    render();
    // Straight into capturing, since an empty row is not worth looking at.
    var caps = $("hotkeys").querySelectorAll(".keycap");
    if (caps.length) listenOn(caps[caps.length - 1], rows.length - 1);
  });

  $("read-slc").addEventListener("click", function () { ask("read_slc_buttons"); });

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
