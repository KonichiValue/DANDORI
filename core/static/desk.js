(function () {
  var key = "__DESK_KEY__";
  if (location.protocol !== "http:" || key.indexOf("DESK_KEY") > -1) return;
  var runners = [].slice.call(document.querySelectorAll("[data-run]"));
  var login = document.getElementById("login");
  var link = document.getElementById("login-link");
  var note = document.getElementById("refresh-note");
  var syncBtn = document.getElementById("sync-btn");
  var syncDot = document.getElementById("sync-dot");
  var syncPop = document.getElementById("sync-pop");
  var scoutNowBtn = document.getElementById("scout-now");
  runners.forEach(function (b) { b.hidden = false; });
  if (syncBtn) syncBtn.hidden = false;

  function setSyncTone(tone) {
    if (syncDot) syncDot.className = "sync-dot " + (tone || "ok");
    if (syncBtn) {
      syncBtn.className = "sync-btn" + (tone === "run" ? " busy" : tone === "bad" ? " bad" : tone === "warn" ? " warn" : "");
    }
  }

  function say(text, tone) {
    note.textContent = text || "";
    note.className = "refresh-note" + (tone === "bad" ? " bad" : "");
    var dotTone = tone === "bad" ? "bad" : tone === "warn" ? "warn"
      : (typeof autoMins === "number" && autoMins <= 0 ? "warn" : "ok");
    setSyncTone(dotTone);
  }

  // A run in progress, as a chip rather than a sentence: which button, how
  // long, what it is doing now and how many steps it has taken. The step is
  // the server's plain phrase for the last tool call, never the model's
  // narration, so it reads "Reading the Slack thread" rather than half of
  // "Now let me check whether the".
  function progress(s) {
    var secs = s.seconds || 0;
    var clock = Math.floor(secs / 60) + ":" + ("0" + (secs % 60)).slice(-2);
    var title = { refresh: "Refresh", delta: "Delta", prep: "Prep" }[s.job] || "Working";
    var doing = s.last || s.message || "Starting";
    var parts = [["rn-time", clock], ["rn-doing", doing.slice(0, 70)]];
    if (s.steps) parts.push(["rn-n", s.steps + " step" + (s.steps > 1 ? "s" : "")]);
    chip(title, s.message || "", parts);
  }

  // The same chip for a sign-in walk, which has phases rather than steps.
  function signingChip(text) {
    chip("Sign-in", text, [["rn-doing", text]]);
  }

  // Built from nodes and textContent rather than an HTML string: what it shows
  // is the server's and the CLI's words, which are never markup.
  function chip(title, hover, parts) {
    setSyncTone("run");
    note.className = "refresh-note run";
    note.title = hover;
    note.textContent = "";
    var dot = document.createElement("span");
    dot.className = "rn-dot";
    dot.setAttribute("aria-hidden", "true");
    note.appendChild(dot);
    var job = document.createElement("b");
    job.className = "rn-job";
    job.textContent = title;
    note.appendChild(job);
    parts.forEach(function (p) {
      var span = document.createElement("span");
      span.className = p[0];
      span.textContent = String(p[1]);
      note.appendChild(span);
    });
  }

  function ready() {
    runners.forEach(function (b) {
      b.disabled = false;
      b.textContent = b.dataset.label || b.textContent;
    });
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // A link that came from outside (a calendar invite, a CLI's output) goes in an
  // href only as http(s), so a javascript: URL never reaches a click.
  function safeUrl(u) {
    u = String(u == null ? "" : u).trim();
    return /^https?:\/\//i.test(u) ? u : "";
  }

  // The questions this page is waiting on, by id, each with the card it was
  // asked from. Several can be out at once, one per card or three on one, and
  // each is a bubble in its own card's thread rather than one strip at the
  // bottom of the page. `tries` counts the redraws that still showed it running,
  // which happens for a second after a Stop while the process winds down.
  var mine = {};
  // This page started a refresh or a prep, so a "done" belongs to us.
  var started = false;
  var bootChecked = false;
  var lastCheckedEpoch = 0;
  var autoMins = 15;
  var outboundAt = 0;
  var scoutWait = null;

  function cancelOutbound() {
    outboundAt = 0;
    if (scoutWait) { clearTimeout(scoutWait); scoutWait = null; }
  }

  function triggerScout() {
    if (autoMins <= 0 || started || pending()) return;
    fetch("/api/scout?k=" + encodeURIComponent(key), { method: "POST" })
      .then(function () { later(400); })
      .catch(function () {});
  }

  function pending() {
    for (var k in mine) if (Object.hasOwn(mine, k)) return true;
    return false;
  }

  function bubbleFor(id) {
    return document.querySelector ? document.querySelector('.qa[data-qa="' + id + '"]') : null;
  }

  function clock(secs) {
    return secs >= 60 ? Math.floor(secs % 3600 / 60) + "m " + (secs % 60) + "s" : secs + "s";
  }

  // One bubble's live line. The CLI usually holds its text to the end, so most
  // of the wait is the count and the dots, and the second line is there to say a
  // long wait is still a normal one.
  function thinking(id, s, queued) {
    var all = document.querySelectorAll ? document.querySelectorAll('.qa[data-qa="' + id + '"]') : [];
    if (all.length > 1) {
      [].forEach.call(all, function (one) { thinkingIn(one, s, queued, id); });
      return;
    }
    thinkingIn(bubbleFor(id), s, queued, id);
  }
  function thinkingIn(li, s, queued, id) {
    if (!li) return;
    var t = li.querySelector(".ask-wait-t");
    var tail = li.querySelector(".ask-wait-tail");
    if (!t || !tail) return;
    li.classList.toggle("queued", !!queued);
    if (queued) {
      t.textContent = "Queued, behind the one in front";
      tail.textContent = "It starts on its own. You can ask on other cards meanwhile.";
      tail.hidden = false;
      return;
    }
    // Its own run's progress, not the header's: several can think at once.
    var run = (s.live || {})[id] || {};
    var secs = run.seconds || 0;
    t.textContent = secs ? "Thinking, " + clock(secs) : "Thinking";
    var word = run.last || (secs > 150
      ? "Still going. Anything that reads a thread takes a few minutes."
      : secs > 40 ? "Reading the ticket, the threads and what you asked before." : "");
    tail.textContent = word;
    tail.hidden = !word;
  }

  // One timer, so there is only ever one loop. Several branches used to schedule
  // the next poll themselves and the login path called poll() outright, which is
  // two loops running, then four, each one polling the server on its own clock.
  var timer = null;
  function later(ms) {
    if (timer) clearTimeout(timer);
    // Nothing worth watching while the tab is in the background. The check
    // happens the moment the owner comes back to it, which is exactly when they have just
    // typed something into a terminal.
    timer = document.hidden ? null : setTimeout(poll, ms);
  }

  // The stamp the page was drawn from. Everything that moves the board writes it
  // and moves the stamp with it: `./tick.py 26` in a terminal, a sweep on a
  // schedule, an ask answered in another tab. None of those come through this
  // page, and a desk quietly a version behind is the one failure this repo
  // exists to prevent.
  var drawnFrom = document.body.dataset.stamp || "";
  // And the server that drew it. A restarted server has a new key, so this page's
  // buttons would all be refused; reloading is how it gets the new one.
  var boot = "__DESK_BOOT__";
  var changed = document.getElementById("changed");
  function reloadKeepingPlace() {
    if (window.deskPlace && window.deskPlace.saveBeforeReload) {
      window.deskPlace.saveBeforeReload();
    }
    location.reload();
  }
  if (changed) changed.addEventListener("click", function () { reloadKeepingPlace(); });

  function safeToReload() {
    // Everything else survives a reload: the open tab is in sessionStorage, the
    // folds are in localStorage, the browser puts the scroll back. Half a
    // sentence in a composer does not, so that is the one thing worth stopping
    // for. Losing what the owner was typing to a helpful refresh would be worse than
    // the staleness it fixes.
    var half = [].slice.call(document.querySelectorAll(".ask textarea"))
      .concat([].slice.call(document.querySelectorAll(".chat-box textarea")))
      .some(function (t) { return t.value.trim(); })
      || !!document.querySelector(".ask-imgs:not([hidden])");
    // The reply line under an answer is always there, so only an open box at
    // the foot of a card counts as open: an empty reply line is not theirs.
    var open = document.querySelector(".ask-box:not([hidden])");
    var pal = document.getElementById("pal");
    var help = document.getElementById("help");
    var syncOpen = syncPop && !syncPop.hidden;
    var over = (pal && !pal.hidden) || (help && help.open) || syncOpen;
    return !half && !open && !over && !ticking();
  }

  // The board alone, without the conversation. When only the conversation
  // moved and it is ours, the answer is swapped into its card in place: a
  // reload for that is what used to put the owner somewhere else on the board.
  var drawnBoard = document.body.dataset.board || "";

  function boardMoved(s) {
    var restarted = s.boot && boot.indexOf("DESK_BOOT") < 0 && s.boot !== boot;
    var moved = s.stamp && drawnFrom && s.stamp !== drawnFrom;
    if (!restarted && !moved) return false;
    var talkOnly = !restarted && s.board && drawnBoard && s.board === drawnBoard;
    if (talkOnly && pending()) {
      // Our own question moving from queued to running. The bubble already
      // says so; there is nothing to redraw.
      drawnFrom = s.stamp;
      return false;
    }
    if (talkOnly && Date.now() < quietUntil) {
      // Ours, a few seconds ago: a Stop written down, a question forgotten. The
      // card already shows it. Redraw what we touched rather than the page.
      drawnFrom = s.stamp;
      for (var ref in touched) if (Object.hasOwn(touched, ref)) redraw(ref);
      touched = {};
      return false;
    }
    // A job the owner has just pressed, with its Undo still on screen. The row already
    // shows the move; reloading would take the Undo away. The poll comes back
    // the moment the toast goes.
    if (!restarted && ticking()) return false;
    if (safeToReload()) {
      say(restarted ? "The desk server restarted, reloading" : "The board changed, reloading");
      reloadKeepingPlace();
      return true;
    }
    // Mid-sentence. Offer the reload rather than taking it.
    if (changed) changed.hidden = false;
    return false;
  }

  function poll() {
    timer = null;
    fetch("/api/status").then(function (r) { return r.json(); }).then(function (s) {
      // My own questions first, found by their ids. Watching the one global job
      // state is wrong as soon as two things are in flight: an ask that finished
      // while something else was starting left a card spinning on "Sending"
      // with the answer already written to disk.
      var asks = s.asks || {};
      var out = [];
      for (var id in mine) {
        if (!Object.hasOwn(mine, id)) continue;
        if (asks[id]) thinking(id, s, asks[id] === "queued");
        else out.push(id);
      }
      if (out.length) {
        landed(out, s);
        return;
      }
      if (pending()) {
        if (boardMoved(s)) return;
        later(900);
        return;
      }
      if (s.state === "running") {
        // A sweep reads every ticket and every thread behind it, so minutes are
        // normal. Saying how long it has been and what it last said is the
        // difference between a slow job and a job that looks stuck.
        progress(s);
        later(2000);
        return;
      }
      if (s.state === "done") {
        // Only reload for something this page started. The job state survives the
        // job, so a plain page load finding an old "done" would reload forever.
        if (!started) { ready(); if (!boardMoved(s)) { say(s.message); later(2500); } return; }
        started = false;
        say("Done, reloading");
        reloadKeepingPlace();
        return;
      }
      // A sign-in walk in progress: fetching the credential, waiting on a
      // browser approval, checking the tools. It is work, not a fault, so it
      // reads as progress, and the buttons wait for it rather than starting a
      // run the server would refuse.
      if (s.state === "signing_in") {
        runners.forEach(function (o) { o.disabled = true; });
        login.hidden = false;
        login.disabled = true;
        login.textContent = "Signing in\u2026";
        if (safeUrl(s.url)) { link.href = safeUrl(s.url); link.hidden = false; }
        else link.hidden = true;
        signingChip(s.message || "Signing in");
        syncPopoverConnections(s);
        later(1500);
        return;
      }
      login.disabled = false;
      login.textContent = "Sign in";
      // Signed out is not a failure, it is one click. Offer the click, and the
      // link the CLI printed once it has one. Keep polling so the moment the
      // browser approval lands the buttons come back on their own.
      if (s.state === "needs_login") {
        ready();
        login.hidden = false;
        if (safeUrl(s.url)) { link.href = safeUrl(s.url); link.hidden = false; }
        say(s.message, "bad");
        syncPopoverConnections(s);
        later(3000);
        return;
      }
      if (s.state === "failed") {
        started = false;
        ready(); say(s.message, "bad");
        syncPopoverConnections(s);
        later(2500);
        return;
      }
      login.hidden = true;
      link.hidden = true;
      ready();
      if (typeof s.auto_mins === "number") autoMins = s.auto_mins;
      if (s.checked_epoch) lastCheckedEpoch = s.checked_epoch;
      if (s.cal_ago && document.querySelectorAll) {
        [].forEach.call(document.querySelectorAll("[data-cal-sync]"), function (cb) {
          cb.textContent = "Calendar " + s.cal_ago;
        });
      }
      syncPopoverConnections(s);
      // Idle, which is where the page spends nearly all of its life and where it
      // used to stop looking. Keep a slow heartbeat so a change made anywhere
      // else arrives on its own.
      if (boardMoved(s)) return;
      if (s.scout && s.scout.state === "running") {
        bootChecked = true;
        chip("Checking", s.scout.said || "Checking Asana and Slack\u2026",
          [["rn-doing", "Asana & Slack\u2026"]]);
        later(1800);
        return;
      }
      if (!bootChecked) {
        bootChecked = true;
        var isStale = s.sync_tone === "warn"
          || (lastCheckedEpoch > 0 && autoMins > 0 && (Date.now() / 1000 - lastCheckedEpoch) >= autoMins * 60);
        if (isStale && autoMins > 0) {
          var nowD = new Date(), wd0 = nowD.getDay(), hr0 = nowD.getHours();
          if (wd0 >= 1 && wd0 <= 5 && hr0 >= 8 && hr0 < 20) {
            say(s.message || "Stale", "warn");
            triggerScout();
            return;
          }
        }
      }
      say(s.message || "Up to date", s.sync_tone === "warn" ? "warn" : "");
      later(2500);
    }).catch(function () {
      // One dropped poll is not a finished job. This used to have no catch at
      // all, so a single blip killed the loop and the card span until reload.
      if (pending() || started) { later(3000); return; }
      say("lost the desk server, retrying", "bad");
      later(5000);
    });
  }

  runners.forEach(function (b) {
    b.dataset.label = b.textContent;
    b.addEventListener("click", function () {
      closeSyncPop();
      runners.forEach(function (o) { o.disabled = true; });
      b.textContent = b.dataset.busy;
      started = true;
      say("Starting\u2026");
      fetch(b.dataset.run + "?k=" + encodeURIComponent(key), { method: "POST" })
        .then(function (r) {
          if (r.status !== 409) return poll();
          // Refused, because something is already going. Say what, rather than
          // putting the button back as if the press had never happened.
          return r.json().then(function (j) {
            started = false;
            if (j.state !== "running") ready();
            say(j.message || "Something is already running", "bad");
            later(1500);
          });
        })
        .catch(function () { ready(); say("could not reach the desk server", "bad"); });
    });
  });

  var lastStatusSnap = null;
  function startMcpSignIn(keepPopoverOpen) {
    if (!keepPopoverOpen) closeSyncPop();
    login.disabled = true;
    login.textContent = "Signing in\u2026";
    runners.forEach(function (o) { o.disabled = true; });
    signingChip("Starting the sign-in");
    syncPopoverConnections({
      state: "signing_in",
      message: "Checking connections\u2026",
      db: lastStatusSnap && lastStatusSnap.db ? lastStatusSnap.db : undefined
    });
    fetch("/api/login?k=" + encodeURIComponent(key), { method: "POST" })
      .then(function () { later(300); })
      .catch(function () {
        login.disabled = false;
        login.textContent = "Sign in";
        ready();
        say("could not reach the desk server", "bad");
      });
  }

  login.addEventListener("click", function () {
    startMcpSignIn(false);
  });

  // Unified Sync & Activity Popover (#sync-pop) and one-click connection buttons.
  function closeSyncPop() {
    if (!syncPop || syncPop.hidden) return;
    syncPop.hidden = true;
    if (syncBtn) syncBtn.setAttribute("aria-expanded", "false");
  }

  function syncPopoverConnections(s) {
    lastStatusSnap = s || lastStatusSnap;
    if (!syncPop) return;
    var spTitle = document.getElementById("sp-title");
    var spSub = document.getElementById("sp-sub");
    if (spTitle && spSub) {
      if (autoMins > 0) {
        spTitle.textContent = "Auto-sync active";
        spSub.textContent = "Checks Asana & Slack every " + autoMins + "m on weekdays, and after posting";
      } else {
        spTitle.textContent = "Manual sync mode";
        spSub.textContent = "Auto-sync is off in Settings; use Check now or Full refresh";
      }
    }
    var mcpDot = document.getElementById("sp-mcp-dot");
    var mcpSaid = document.getElementById("sp-mcp-said");
    var mcpBtn = document.getElementById("sp-mcp-btn");
    var mcpUrl = document.getElementById("sp-mcp-url");
    if (mcpDot && mcpSaid && mcpBtn) {
      var isSigning = s.state === "signing_in";
      var needMcp = s.state === "needs_login";
      mcpDot.className = "sp-cdot " + (isSigning ? "is-run" : needMcp ? "is-bad" : "is-ok");
      var mcpText = isSigning ? (s.message || "Signing in\u2026") : (needMcp ? "Needs sign-in" : "Connected");
      mcpSaid.textContent = mcpText;
      mcpSaid.title = mcpText;
      mcpBtn.hidden = !(needMcp || isSigning);
      mcpBtn.disabled = isSigning;
      mcpBtn.textContent = isSigning ? "Signing in\u2026" : "Sign in";
      if (mcpUrl) {
        var u = isSigning ? safeUrl(s.url) : "";
        if (u) { mcpUrl.href = u; mcpUrl.hidden = false; }
        else mcpUrl.hidden = true;
      }
    }
    var db = s.db || {};
    var dbRow = document.getElementById("sp-db-row");
    var dbDot = document.getElementById("sp-db-dot");
    var dbSaid = document.getElementById("sp-db-said");
    var dbBtn = document.getElementById("sp-db-btn");
    if (dbRow && dbDot && dbSaid && dbBtn) {
      dbRow.hidden = !db.configured;
      if (db.configured) {
        var dbOk = db.state === "ok";
        var dbRun = db.state === "running";
        dbDot.className = "sp-cdot " + (dbOk ? "is-ok" : dbRun ? "is-run" : "is-warn");
        var dbText = db.said || (dbOk ? "Connected" : "Session expired");
        dbSaid.textContent = dbText;
        dbSaid.title = dbText;
        dbBtn.hidden = dbOk;
        dbBtn.disabled = dbRun;
        dbBtn.textContent = dbRun ? "Checking\u2026" : "Log in";
      }
    }
    if (s.calendar && s.calendar.said) {
      var calSaid = document.getElementById("sp-cal-said");
      if (calSaid && s.calendar.state === "running") calSaid.textContent = "Syncing\u2026";
    }
  }

  function loadActivity() {
    var listEl = document.getElementById("sp-runs");
    if (!listEl) return;
    fetch("/api/activity").then(function (r) { return r.json(); }).then(function (j) {
      var spLast = document.getElementById("sp-last");
      if (spLast) spLast.textContent = j.last_full ? "Full sweep " + j.last_full : "No full sweep yet today";
      if (j.cal_ago) {
        var calSaid = document.getElementById("sp-cal-said");
        if (calSaid) calSaid.textContent = j.cal_ago;
      }
      if (j.db) {
        var cur = lastStatusSnap || { state: login && !login.hidden ? "needs_login" : "idle" };
        syncPopoverConnections({ state: cur.state, message: cur.message, url: cur.url, db: j.db });
      }
      var runs = j.runs || [];
      if (!runs.length) {
        listEl.innerHTML = '<li class="sp-empty">No recent runs logged today.</li>';
        return;
      }
      listEl.innerHTML = runs.map(function (r) {
        var took = r.took >= 60 ? Math.floor(r.took / 60) + "m " + (r.took % 60) + "s" : r.took + "s";
        var bul = (r.bullets || []).map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("");
        return '<li class="sp-run">'
          + '<div class="sp-rmeta"><span class="sp-rkind">' + esc(r.kind) + '</span>'
          + '<span class="sp-rwhen">' + esc(r.when) + '</span>'
          + '<span class="sp-rtook">' + esc(took) + " \u00b7 " + esc(r.calls) + " calls</span></div>"
          + (r.lead ? '<p class="sp-rtxt">' + esc(r.lead) + "</p>" : "")
          + (bul ? '<ul class="sp-rbul">' + bul + "</ul>" : "")
          + "</li>";
      }).join("");
    }).catch(function () {});
  }

  if (syncBtn && syncPop) {
    syncBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var opening = syncPop.hidden;
      syncPop.hidden = !opening;
      syncBtn.setAttribute("aria-expanded", String(opening));
      if (opening) loadActivity();
    });
  }
  if (scoutNowBtn) {
    scoutNowBtn.addEventListener("click", function () {
      closeSyncPop();
      chip("Checking", "Checking Asana and Slack\u2026", [["rn-doing", "Asana & Slack\u2026"]]);
      fetch("/api/scout?force=1&k=" + encodeURIComponent(key), { method: "POST" })
        .then(function () { later(400); })
        .catch(function () { say("could not reach the desk server", "bad"); });
    });
  }
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("#sp-mcp-btn")) {
      startMcpSignIn(true);
      return;
    }
    var dbLoginBtn = e.target.closest && e.target.closest("#sp-db-btn,[data-db-login]");
    if (dbLoginBtn) {
      dbLoginBtn.disabled = true;
      dbLoginBtn.textContent = "Opening browser\u2026";
      var dbSaid = document.getElementById("sp-db-said");
      if (dbSaid) dbSaid.textContent = "Approve Okta in browser\u2026";
      var chkWhen = document.querySelector(".chk-when");
      if (chkWhen && dbLoginBtn.hasAttribute("data-db-login")) {
        chkWhen.textContent = "Approve Okta login in your browser\u2026";
      }
      fetch("/api/db-login?k=" + encodeURIComponent(key), { method: "POST" })
        .then(function () { later(1000); setTimeout(function () { dbLoginBtn.disabled = false; }, 6000); })
        .catch(function () { dbLoginBtn.disabled = false; dbLoginBtn.textContent = "Log in"; });
      return;
    }
    var calBtn = e.target.closest && e.target.closest("#sp-cal-btn");
    if (calBtn) {
      calBtn.disabled = true;
      var calSaid = document.getElementById("sp-cal-said");
      if (calSaid) calSaid.textContent = "Syncing\u2026";
      fetch("/api/calendar?k=" + encodeURIComponent(key), { method: "POST" })
        .then(function () { setTimeout(function () { calBtn.disabled = false; loadActivity(); }, 4000); })
        .catch(function () { calBtn.disabled = false; });
      return;
    }
    if (e.target.closest && e.target.closest("[data-open-conn]")) {
      closeSyncPop();
      var setDlg = document.getElementById("settings");
      if (setDlg && !setDlg.open) setDlg.showModal();
      return;
    }
    if (syncPop && !syncPop.hidden && !(e.target.closest && e.target.closest(".sync-wrap"))) {
      closeSyncPop();
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && syncPop && !syncPop.hidden) {
      closeSyncPop();
    }
  });

  // Test connections, in Settings. It lives here rather than in base.js because
  // it needs the server key, and base.js is the half of the front end that runs
  // on a page opened from disk with no server behind it.
  //
  // The check takes about twenty seconds (most of it starting a headless run to
  // count its tools), so the server does it in a thread and this polls
  // /api/status for the rows as they land. Every row arrives as it is decided,
  // rather than all six at the end, because a panel that shows the credential
  // result in one second has already answered the usual question.
  //
  // Bound on the document rather than on the button, because this script runs
  // inline in the header while the page is still parsing: the Settings dialog
  // is at the end of the body and does not exist yet. The same reason the ask
  // sends are delegated a few lines down.
  (function () {
    var list, when, note, go;
    var watching = null;
    function parts() {
      go = document.querySelector("[data-check]");
      list = document.querySelector(".chk-rows");
      when = document.querySelector(".chk-when");
      note = document.querySelector(".chk-note");
      return !!(go && list && when && note);
    }

    function draw(h) {
      if (!parts()) return;
      var rows = h.rows || [];
      list.innerHTML = rows.map(function (r) {
        var mark = r.ok === true ? "ok" : r.ok === false ? "bad" : "unsure";
        var glyph = r.ok === true ? "\u2713" : r.ok === false ? "\u2715" : "?";
        return '<li class="chk ' + mark + '"><span class="chk-m">' + glyph + "</span>"
          + '<span class="chk-b"><b>' + esc(r.name) + "</b>" + esc(r.said || "")
          + (r.fix ? '<i>' + esc(r.fix) + "</i>" : "")
          + "</span></li>";
      }).join("");
      note.textContent = h.note || "";
      if (h.state === "running") {
        when.textContent = "Checking\u2026 " + rows.length + " of about 6";
      } else if (h.state === "done") {
        when.textContent = "Checked at " + (h.at || "");
        go.disabled = false;
        go.textContent = "Test again";
        if (watching) { clearInterval(watching); watching = null; }
      }
    }

    function esc(s) {
      return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
      });
    }

    function follow() {
      fetch("/api/status").then(function (r) { return r.json(); }).then(function (s) {
        if (s.health) draw(s.health);
      }).catch(function () {
        if (parts()) {
          when.textContent = "lost the desk server";
          go.disabled = false;
        }
        if (watching) { clearInterval(watching); watching = null; }
      });
    }

    document.addEventListener("click", function (e) {
      if (!e.target.closest("[data-check]")) return;
      if (!parts()) return;
      go.disabled = true;
      go.textContent = "Checking";
      note.textContent = "";
      list.innerHTML = "";
      when.textContent = "Starting\u2026";
      fetch("/api/check?k=" + encodeURIComponent(key), { method: "POST" })
        .then(function () {
          if (!watching) watching = setInterval(follow, 900);
          follow();
        })
        .catch(function () {
          go.disabled = false;
          go.textContent = "Test connections";
          when.textContent = "could not reach the desk server";
        });
    });
  })();

  // Asking from a card. The question travels with the job it was asked about,
  // so the server can hand the agent the ticket, the item and its draft, and
  // the answer comes back onto the same card.
  //
  // Delegated, and the buttons are shown once the cards exist. This script runs
  // from the header while the page is still parsing, so the jobs below it are
  // not in the document yet: reaching for them here found nothing, which left
  // every card with the clipboard fallback and no way to actually send.
  function showSends() {
    [].forEach.call(document.querySelectorAll("[data-ask-send],[data-chat-toggle],[data-chat-open],[data-chat-cursor]"), function (b) {
      b.hidden = false;
    });
    [].forEach.call(document.querySelectorAll(".ask-copy"), function (b) {
      b.dataset.liveCursor = "1";
      b.textContent = "Open in Cursor \u2197";
      b.title = "Open Cursor in DANDORI with this job and your typed prompt preloaded (Option-click to copy instead)";
    });
    // A question still running when the page was drawn, from before a reload
    // or from another tab. Watch it like one asked here, so its answer lands in
    // place rather than the bubble sitting on "Thinking" until the next reload.
    [].forEach.call(document.querySelectorAll(".qa[data-live]"), function (li) {
      var box = li.closest(".ask");
      if (box && li.dataset.qa && !mine[li.dataset.qa]) mine[li.dataset.qa] = { ref: box.dataset.ask };
    });
    if (pending()) later(0);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", showSends);
  } else {
    showSends();
  }

  // Forgetting a question. The record is the owner's, so it is theirs to throw away, and
  // the bubble goes as soon as the server says it is gone rather than on the
  // next rebuild.
  document.addEventListener("click", function (e) {
    var x = e.target.closest && e.target.closest("[data-forget]");
    if (!x) return;
    var bubble = x.closest(".qa");
    x.disabled = true;
    hush();
    var card = bubble.closest(".ask");
    if (card) touched[card.dataset.ask] = true;
    fetch("/api/forget?k=" + encodeURIComponent(key), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: x.dataset.forget })
    }).then(function (r) {
      if (!r.ok) throw new Error("the desk server kept it");
      var list = bubble.parentNode;
      var fold = list.closest(".qa-earlier");
      bubble.remove();
      if (!list.querySelector(".qa")) list.remove();
      if (fold && !fold.querySelector(".qa")) fold.remove();
    }).catch(function (err) {
      x.disabled = false;
      say(err.message, "bad");
    });
  });

  // An answer landed. Put it into the card it was asked from, in place, and
  // keep whatever the owner was reading where it was on screen. A reload did the
  // same job and dropped them somewhere else on the board, which is what they
  // asked to stop. If the answer also rewrote the board (a draft changed, an item moved)
  // that card needs redrawing from the board, and that is a reload.
  function landed(ids, s) {
    var refs = {};
    ids.forEach(function (id) {
      refs[mine[id].ref] = true;
      delete mine[id];
    });
    if (s.board && drawnBoard && s.board !== drawnBoard) {
      say("Answered, and it changed the board");
      if (!boardMoved(s)) later(2500);
      return;
    }
    say("Answered");
    hush(s);
    var done = 0, want = 0;
    for (var ref in refs) if (Object.hasOwn(refs, ref)) want += 1;
    for (var r in refs) {
      if (!Object.hasOwn(refs, r)) continue;
      redraw(r, ids).then(function () {
        done += 1;
        if (done === want) later(pending() ? 900 : 2500);
      });
    }
  }

  // Swallow the next few seconds of conversation-only changes, which are ours:
  // the answer we just drew, a Stop being written down, a question forgotten.
  // Anything that touches the board itself still reloads.
  var quietUntil = 0;
  var touched = {};
  function hush(s) {
    quietUntil = Date.now() + 15000;
    if (s && s.stamp) drawnFrom = s.stamp;
  }

  // One card's conversation, fetched and swapped in. What the owner has typed into a
  // reply line survives it, and the screen does not move.
  // One conversation's markup, or null when the server would not draw it.
  function thread(ref, chat) {
    return fetch("/api/thread?ref=" + encodeURIComponent(ref) + (chat ? "&chat=1" : "")).then(function (r) {
      return r.ok ? r.text() : null;
    }).catch(function () { return null; });
  }
  function redraw(ref, fresh) {
    var panel = chatBody();
    var both = [thread(ref, false)];
    if (panel && panel.dataset.ask === ref) both.push(thread(ref, true));
    return Promise.all(both).then(function (got) {
      var html = got[0];
      if (html === null) throw new Error("no thread");
      var place = window.deskPlace ? window.deskPlace.take() : null;
      if (got.length > 1 && got[1] !== null) drawChat(got[1], fresh);
      [].forEach.call(document.querySelectorAll(".ask[data-ask]"), function (box) {
        if (box.dataset.ask !== ref || box.dataset.chat) return;
        var thread = box.querySelector(".ask-thread");
        if (!thread) return;
        var kept = {};
        [].forEach.call(thread.querySelectorAll(".fu"), function (fu) {
          var t = fu.querySelector("textarea");
          if (t && t.value.trim()) kept[fu.dataset.parent] = t.value;
        });
        thread.innerHTML = html;
        for (var k in kept) {
          var back = thread.querySelector('.fu[data-parent="' + k + '"] textarea');
          if (back) back.value = kept[k];
        }
        (fresh || []).forEach(function (id) {
          var li = thread.querySelector('.qa[data-qa="' + id + '"]');
          if (li) li.classList.add("fresh");
        });
        var n = thread.querySelectorAll(".qa").length;
        var count = box.querySelector(".ask-n");
        if (count) count.textContent = n + " asked";
      });
      showSends();
      if (window.deskPlace) window.deskPlace.put(place);
    }).catch(function () {
      // No thread to swap in, so fall back to what this used to do: reload,
      // unless the owner is mid-sentence, in which case offer it.
      if (safeToReload()) reloadKeepingPlace();
      else if (changed) changed.hidden = false;
    });
  }

  // The bubble for a question that has just gone, drawn here so it is on the
  // card the instant the owner presses Enter rather than when the server gets round to
  // it. It is the same markup the server draws for a running question, so the
  // redraw that replaces it changes nothing the owner can see until the answer is in.
  function runningBubble(question, id, model) {
    var li = document.createElement("li");
    li.className = "qa running fresh";
    li.dataset.qa = id || "";
    li.dataset.live = "1";
    li.innerHTML =
      '<div class="qa-me"><span class="qa-k">You asked</span>' +
      '<span class="qa-q">' + esc(question) + "</span>" +
      '<span class="qa-when">just now</span></div>' +
      '<div class="qa-wait">' +
      '<span class="ask-dots" aria-hidden="true"><i></i><i></i><i></i></span>' +
      '<span class="ask-wait-t">Sending</span>' +
      (model ? '<span class="qa-model">' + esc(model) + "</span>" : "") +
      '<button type="button" class="qa-stop" data-stop="' + esc(id || "") + '">Stop</button>' +
      '<span class="ask-wait-tail" hidden></span></div>';
    return li;
  }

  // Where a new question's bubble goes. A new question about the job goes at
  // the end of the card's thread; a reply goes under the exchange it replies
  // to, and the reply line moves down with it, the way the server draws it.
  function placeBubble(box, panel, li) {
    var thread = box.querySelector(".ask-thread");
    var empty = thread && thread.querySelector(".chat-empty");
    if (empty) empty.remove();
    if (panel.classList.contains("fu")) {
      var of = panel.closest(".qa");
      var list = of && of.classList.contains("kid") ? of.parentNode : null;
      if (!list) {
        var det = panel.closest("details");
        list = det && det.querySelector(".qa-kids");
        if (!list && det) {
          list = document.createElement("ul");
          list.className = "qa-kids";
          det.insertBefore(list, panel);
        }
      }
      if (list) { list.appendChild(li); panel.hidden = true; return; }
    }
    var lists = thread ? [].filter.call(thread.children, function (c) {
      return c.classList.contains("qa-list");
    }) : [];
    var root = lists[lists.length - 1];
    if (!root && thread) {
      root = document.createElement("ul");
      root.className = "qa-list";
      thread.appendChild(root);
    }
    if (root) root.appendChild(li);
  }

  function failBubble(li, message) {
    li.className = "qa failed";
    var wait = li.querySelector(".qa-wait");
    if (wait) wait.remove();
    var bad = document.createElement("div");
    bad.className = "qa-bad";
    bad.innerHTML = "<b>That did not go</b>" + esc(message) +
      '<button type="button" class="qa-retry" data-retry="">Try again</button>';
    li.appendChild(bad);
  }

  // Send one question, from a fresh bubble or from Try again on an old one.
  // The model the owner picked next to the box. Every picker on the page shows the
  // same one, because it is one pref, so whichever is nearest will do.
  function picker(scope) {
    var sel = (scope && scope.querySelector && scope.querySelector(".ask-model select")) ||
      (document.querySelector ? document.querySelector(".ask-model select") : null);
    return sel || null;
  }
  function modelName(sel) {
    if (!sel || !sel.options) return "";
    var o = sel.options[sel.selectedIndex];
    return o ? o.textContent.replace(/\s*\(default\)$/, "") : "";
  }
  document.addEventListener("change", function (e) {
    var sel = e.target.closest && e.target.closest(".ask-model select");
    if (!sel) return;
    [].forEach.call(document.querySelectorAll(".ask-model select"), function (o) {
      if (o !== sel) o.value = sel.value;
    });
    if (window.deskPref) window.deskPref.set("desk-ask-model", sel.value);
  });

  function copyTextToClipboard(s) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(s).catch(function () { fallbackCopyText(s); });
    }
    fallbackCopyText(s);
    return Promise.resolve();
  }
  function fallbackCopyText(s) {
    var a = document.createElement("textarea");
    a.value = s;
    a.setAttribute("readonly", "");
    a.style.cssText = "position:fixed;top:-1000px";
    document.body.appendChild(a);
    a.select();
    try { document.execCommand("copy"); } finally { document.body.removeChild(a); }
  }

  function panelImages(panel) {
    if (!panel) return [];
    if (!panel._imgs) panel._imgs = [];
    return panel._imgs;
  }
  function renderPanelImages(panel) {
    var tray = panel && panel.querySelector && panel.querySelector(".ask-imgs");
    if (!tray) return;
    var imgs = panelImages(panel);
    if (!imgs.length) {
      tray.innerHTML = "";
      tray.hidden = true;
      return;
    }
    tray.hidden = false;
    tray.innerHTML = imgs.map(function (im, idx) {
      return '<span class="ask-img-chip">' +
        '<img src="' + esc(im.data) + '" alt="">' +
        '<span>' + esc(im.name || ("image-" + (idx + 1))) + '</span>' +
        '<button type="button" data-rm-img="' + idx + '" aria-label="Remove image">&times;</button>' +
        '</span>';
    }).join("");
  }
  function attachImageFiles(panel, files) {
    var imgs = panelImages(panel);
    [].slice.call(files || []).forEach(function (file) {
      if (!file || !/^image\/(png|jpeg|jpg|webp|gif)$/i.test(file.type || "")) return;
      if (imgs.length >= 4 || file.size > 5 * 1024 * 1024) return;
      var reader = new FileReader();
      reader.onload = function () {
        if (typeof reader.result === "string" && imgs.length < 4) {
          imgs.push({ name: file.name || "screenshot.png", data: reader.result });
          renderPanelImages(panel);
        }
      };
      reader.readAsDataURL(file);
    });
  }
  document.addEventListener("paste", function (e) {
    var panel = e.target && e.target.closest && (
      e.target.closest(".ask-box") || e.target.closest(".fu") || e.target.closest(".chat-box")
    );
    if (!panel || !e.clipboardData || !e.clipboardData.items) return;
    var files = [];
    [].slice.call(e.clipboardData.items).forEach(function (it) {
      if (it.kind === "file" && /^image\//i.test(it.type || "")) {
        var f = it.getAsFile();
        if (f) files.push(f);
      }
    });
    if (files.length) {
      e.preventDefault();
      attachImageFiles(panel, files);
    }
  });
  document.addEventListener("drop", function (e) {
    var panel = e.target && e.target.closest && (
      e.target.closest(".ask-box") || e.target.closest(".fu") || e.target.closest(".chat-box")
    );
    if (!panel || !e.dataTransfer || !e.dataTransfer.files) return;
    var files = [].slice.call(e.dataTransfer.files).filter(function (f) {
      return /^image\//i.test(f.type || "");
    });
    if (files.length) {
      e.preventDefault();
      attachImageFiles(panel, files);
    }
  });
  document.addEventListener("dragover", function (e) {
    var panel = e.target && e.target.closest && (
      e.target.closest(".ask-box") || e.target.closest(".fu") || e.target.closest(".chat-box")
    );
    if (panel) e.preventDefault();
  });
  document.addEventListener("click", function (e) {
    var rm = e.target && e.target.closest && e.target.closest("[data-rm-img]");
    if (!rm) return;
    var panel = rm.closest(".ask-box") || rm.closest(".fu") || rm.closest(".chat-box");
    if (!panel) return;
    var idx = parseInt(rm.dataset.rmImg, 10);
    var imgs = panelImages(panel);
    if (!isNaN(idx)) imgs.splice(idx, 1);
    renderPanelImages(panel);
  });

  // 1-click "Open in Cursor ↗": writes the full context bundle (ticket, job,
  // numbered drafts, recent Slack/Asana timeline, pasted screenshots, and live
  // DB Account X-Ray) to `clients/<client>/tmp/`, focuses Cursor on the DANDORI
  // workspace, and pre-fills a new Agent chat via Cursor's deeplink. Option-click
  // copies the Markdown bundle to clipboard instead.
  document.addEventListener("click", function (e) {
    var b = e.target && e.target.closest && e.target.closest("[data-ctx-ref],.ask-copy[data-live-cursor],[data-chat-cursor]");
    if (!b || b.disabled) return;
    var inChat = b.closest && b.closest(".chat-box");
    var askBox = inChat ? (typeof chatBody === "function" ? chatBody() : null) : (b.closest && b.closest(".ask"));
    var panel = inChat || (b.closest && (b.closest(".fu") || b.closest(".ask-box"))) || (askBox && askBox.querySelector(".ask-box"));
    var ref = b.dataset.ctxRef || (askBox && askBox.dataset && askBox.dataset.ask) || "board";
    var draftNum = b.dataset.ctxDraft ? parseInt(b.dataset.ctxDraft, 10) : 0;
    var field = panel && panel.querySelector && panel.querySelector("textarea");
    var question = field && field.value ? field.value.trim() : "";
    var imgs = panel ? panelImages(panel).slice() : [];
    var followUp = "";
    var aboutQa = b.closest && b.closest(".qa");
    if (aboutQa) {
      var qEl = aboutQa.querySelector(".qa-q");
      var aEl = aboutQa.querySelector(".qa-a");
      followUp = "Q: " + (qEl ? qEl.textContent.trim() : "") + "\n\nA: " + (aEl ? aEl.textContent.trim() : "");
    }

    var orig = b.textContent;
    b.disabled = true;

    if (e.altKey) {
      b.textContent = "Copying\u2026";
      var ctxUrl = "/api/context?ref=" + encodeURIComponent(ref) + (draftNum ? "&draft=" + encodeURIComponent(draftNum) : "");
      fetch(ctxUrl).then(function (r) {
        if (!r.ok) throw new Error("context failed");
        return r.json();
      }).then(function (j) {
        var md = (j.markdown || "") + (question ? "\n\n## My Request\n\n" + question : "");
        return copyTextToClipboard(md);
      }).then(function () {
        b.textContent = "Copied context!";
        setTimeout(function () { b.disabled = false; b.textContent = orig; }, 1600);
      }).catch(function () {
        b.textContent = "Copy failed";
        setTimeout(function () { b.disabled = false; b.textContent = orig; }, 1600);
      });
      return;
    }

    b.textContent = "Opening Cursor\u2026";
    fetch("/api/open-cursor?k=" + encodeURIComponent(key), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ref: ref,
        draft: draftNum || 0,
        question: question,
        follow_up: followUp,
        images: imgs
      })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || "failed");
        if (j.fallback_copy) {
          return copyTextToClipboard(j.fallback_copy).then(function () {
            b.textContent = "Copied context!";
          });
        }
        b.textContent = "Opened in Cursor!";
      });
    }).then(function () {
      setTimeout(function () { b.disabled = false; b.textContent = orig; }, 1800);
    }).catch(function () {
      b.textContent = "Could not open";
      setTimeout(function () { b.disabled = false; b.textContent = orig; }, 1800);
    });
  });

  // Clicking a `Draft N` chip inside a step jumps straight to that draft and opens it.
  document.addEventListener("click", function (e) {
    var chipEl = e.target && e.target.closest && e.target.closest("a.ds-dref");
    if (!chipEl) return;
    var href = chipEl.getAttribute("href") || "";
    if (href.charAt(0) !== "#") return;
    var targetEl = document.getElementById(href.slice(1));
    if (!targetEl) return;
    e.preventDefault();
    if (targetEl.tagName === "DETAILS") targetEl.open = true;
    var parentDet = targetEl.closest && targetEl.closest("details.job");
    if (parentDet) parentDet.open = true;
    targetEl.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  function ask(ref, question, parent, li, model, images) {
    var payload = { ref: ref, question: question, parent: parent || "", model: model || "" };
    if (images && images.length) payload.images = images;
    return fetch("/api/ask?k=" + encodeURIComponent(key), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          throw new Error(j.error || j.message || "the desk server said no");
        });
      }
      return r.json().then(function (j) {
        if (!j.id) throw new Error("the desk server lost it");
        mine[j.id] = { ref: ref };
        li.dataset.qa = j.id;
        var stop = li.querySelector(".qa-stop");
        if (stop) stop.dataset.stop = j.id;
        thinking(j.id, {}, !!j.ahead);
        say(j.ahead ? "Queued behind " + j.ahead : "Working on your question…");
        poll();
      });
    });
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest(".ask-send");
    if (!b || b.hidden) return;
    // The panel's composer sits under its conversation rather than inside it,
    // so it is the one send whose box is not its ancestor.
    var inChat = b.closest(".chat-box");
    var box = inChat ? chatBody() : b.closest(".ask");
    if (!box) return;
    // The send inside an answer is a follow-up to that answer; the one at the
    // foot of the card is a new question about the job.
    var panel = inChat || b.closest(".fu") || box.querySelector(".ask-box");
    var field = panel.querySelector("textarea");
    var hint = panel.querySelector(".ask-hint");
    var imgs = panelImages(panel).slice();
    var question = (field.value || "").trim();
    if (!question && !imgs.length) { field.focus(); return; }
    if (!question && imgs.length) question = "Inspect the attached screenshot(s) against this item.";
    if (hint) hint.textContent = "";
    var sel = picker(panel);
    var shownQ = question + (imgs.length ? " [+" + imgs.length + " image" + (imgs.length === 1 ? "" : "s") + "]" : "");
    var li = runningBubble(shownQ, "", modelName(sel));
    li.dataset.parent = panel.dataset.parent || "";
    placeBubble(box, panel, li);
    if (inChat) chatScroll(true);
    // The words are on the card now, in the bubble, so the composer lets go of
    // them: it shuts, and the next question starts on an empty line.
    field.value = "";
    field.style.height = "";
    panel._imgs = [];
    renderPanelImages(panel);
    if (!panel.classList.contains("fu") && !inChat) {
      panel.hidden = true;
      var bar = box.querySelector(".ask-bar");
      if (bar) bar.hidden = false;
    }
    ask(box.dataset.ask, question, li.dataset.parent, li, sel ? sel.value : "", imgs).catch(function (err) {
      failBubble(li, err.message);
    });
  });

  // Try again, on a bubble that failed. Sent as a new question with the same
  // words and the same parent, and the failed one is forgotten once the new one
  // is in the line, so the card does not keep both.
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest(".qa-retry");
    if (!b) return;
    var li = b.closest(".qa");
    var box = li.closest(".ask");
    var q = li.querySelector(".qa-q");
    var old = b.dataset.retry;
    var question = q ? q.textContent.trim() : "";
    if (!box || !question) return;
    var sel = picker(box);
    var fresh = runningBubble(question, "", modelName(sel));
    fresh.dataset.parent = li.dataset.parent || "";
    li.parentNode.replaceChild(fresh, li);
    ask(box.dataset.ask, question, fresh.dataset.parent, fresh, sel ? sel.value : "").then(function () {
      if (!old) return;
      hush();
      return fetch("/api/forget?k=" + encodeURIComponent(key), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: old })
      }).catch(function () { say("could not forget the failed one", "bad"); });
    }).catch(function (err) { failBubble(fresh, err.message); });
  });

  // Stop, on the bubble that is running. It used to be a Cancel that only hid
  // the box, which is the worst version: the owner thinks they have called it off,
  // the agent carries on, and a minute later it rewrites the draft they changed
  // their mind about.
  document.addEventListener("click", function (e) {
    var x = e.target.closest && e.target.closest(".qa-stop");
    if (!x) return;
    var id = x.dataset.stop;
    var li = x.closest(".qa");
    if (!id) return;
    delete mine[id];
    hush();
    var box = li.closest(".ask");
    if (box) touched[box.dataset.ask] = true;
    li.className = "qa cancelled";
    var k = li.querySelector(".qa-k");
    if (k) k.textContent = "Stopped";
    var wait = li.querySelector(".qa-wait");
    if (wait) wait.remove();
    say("Stopped");
    fetch("/api/cancel?k=" + encodeURIComponent(key), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id })
    }).catch(function () { say("could not reach the desk server", "bad"); });
  });

  // The move an answer proposed, now that the owner has pressed it. The server checks
  // it against the answer it saved and runs ./tick.py, which is still the only
  // thing that writes a state, so the move is in the item's history like any
  // other. The board has moved after it, so the card redraws from the board.
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest(".qa-act");
    if (!b || b.disabled) return;
    var row = b.closest(".qa-acts");
    [].forEach.call(row.querySelectorAll(".qa-act"), function (o) { o.disabled = true; });
    b.classList.add("busy");
    fetch("/api/act?k=" + encodeURIComponent(key), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: b.dataset.act, n: b.dataset.actN, verb: b.dataset.actVerb, who: b.dataset.actWho
      })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || "the desk server said no");
        var did = document.createElement("p");
        did.className = "qa-did";
        did.textContent = j.acted_on || "Moved";
        row.parentNode.replaceChild(did, row);
        later(300);
      });
    }).catch(function (err) {
      [].forEach.call(row.querySelectorAll(".qa-act"), function (o) { o.disabled = false; });
      b.classList.remove("busy");
      var k = row.querySelector(".qa-acts-k");
      if (k) { k.textContent = err.message; k.classList.add("bad"); }
    });
  });


  // The side panel: one long conversation beside the page. Same record as the
  // cards, drawn as a chat, so everything here is the card's machinery pointed
  // at a box that does not fold. Each question follows on from the newest
  // answer, which is what makes "why" and "shorter" whole questions, until the owner
  // presses New topic.
  function chatEl() { return document.getElementById("chat"); }
  function chatBody() {
    var c = chatEl();
    return c && c.querySelector ? c.querySelector(".chat-body") : null;
  }
  function chatBox() {
    var c = chatEl();
    return c && c.querySelector ? c.querySelector(".chat-box") : null;
  }
  // The newest answer is what the next question follows on from.
  function follow(fresh) {
    var box = chatBox(), body = chatBody();
    if (!box || !body) return;
    var said = body.querySelectorAll(".qa.answered");
    var lastOne = said.length ? said[said.length - 1] : null;
    box.dataset.parent = lastOne && !box.dataset.fresh ? lastOne.dataset.qa : "";
    var line = box.querySelector(".chat-follow");
    if (line) {
      var q = lastOne && lastOne.querySelector(".qa-q");
      line.hidden = !box.dataset.parent;
      line.textContent = box.dataset.parent && q
        ? "Following on from “" + q.textContent.trim().slice(0, 60) + "”"
        : "";
    }
  }
  function chatScroll(force) {
    var body = chatBody();
    if (!body) return;
    var near = body.scrollHeight - body.scrollTop - body.clientHeight < 160;
    if (force || near) body.scrollTop = body.scrollHeight;
  }
  function drawChat(html, fresh) {
    var body = chatBody();
    if (!body) return;
    var t = body.querySelector(".ask-thread");
    if (!t) return;
    var near = body.scrollHeight - body.scrollTop - body.clientHeight < 160;
    t.innerHTML = html;
    (fresh || []).forEach(function (id) {
      var li = t.querySelector('.qa[data-qa="' + id + '"]');
      if (li) li.classList.add("fresh");
    });
    var box = chatBox();
    if (box) delete box.dataset.fresh;
    follow();
    if (near || (fresh && fresh.length)) body.scrollTop = body.scrollHeight;
  }
  // The strip under the header that says which job, which ticket and where it
  // stands, redrawn from the picked option so it can never name the wrong one.
  function aboutStrip(opt, ref) {
    var c = chatEl();
    var strip = c && c.querySelector(".chat-about");
    if (!strip) return;
    var d = (opt && opt.dataset) || {};
    strip.dataset.about = ref;
    strip.innerHTML = "";
    function bit(cls, text, tag) {
      var s = document.createElement(tag || "span");
      s.className = cls;
      s.textContent = text;
      strip.appendChild(s);
      return s;
    }
    if (d.num) bit("chat-num", d.num);
    bit("chat-tag", d.tag || "");
    bit("chat-title", d.title || (opt ? opt.textContent : ref)).title = d.title || "";
    if (d.state) bit("chat-state " + (d.tone || "grey"), d.state);
    if (d.href) {
      var a = bit("chat-goto", "Card →", "a");
      a.href = "#" + d.href;
      a.title = "Go to its card";
    }
  }
  function chatMenu(on) {
    var c = chatEl();
    var m = c && c.querySelector(".chat-menu");
    if (m) m.hidden = !on;
  }
  // Clear: this conversation, or every one. One confirm, because it cannot be
  // undone and the second item sits one row under the first.
  function chatClear(which) {
    chatMenu(false);
    var body = chatBody();
    if (!body) return;
    var ref = body.dataset.ask;
    var all = which === "all";
    var n = body.querySelectorAll(".qa").length;
    if (!all && !n) { say("nothing to clear here", "ok"); return; }
    if (!window.confirm(all ? "Clear every conversation on the desk, on every card? This cannot be undone."
                            : "Clear this conversation (" + n + " " + (n === 1 ? "question" : "questions") + ")?")) return;
    hush();
    fetch("/api/forget?k=" + encodeURIComponent(key), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(all ? { all: true } : { ref: ref })
    }).then(function (r) {
      if (!r.ok) throw new Error("the desk server kept them");
      return r.json();
    }).then(function (j) {
      var keep = [].slice.call(body.querySelectorAll(".qa.running,.qa.queued"));
      var t = body.querySelector(".ask-thread");
      if (t) {
        t.innerHTML = keep.length ? "" : '<p class="chat-empty">Cleared. Ask anything about this.</p>';
        if (keep.length) {
          var ul = document.createElement("ul");
          ul.className = "qa-list";
          keep.forEach(function (li) { ul.appendChild(li); });
          t.appendChild(ul);
        }
      }
      var box = chatBox();
      if (box) delete box.dataset.fresh;
      follow();
      say("cleared " + (j.forgot || 0), "ok");
      // Every card that held one of them is stale now, so redraw those in place.
      var seen = {};
      [].forEach.call(document.querySelectorAll(".ask[data-ask]"), function (b) {
        var r = b.dataset.ask;
        if (b.dataset.chat || seen[r] || (!all && r !== ref) || !b.querySelector(".qa")) return;
        seen[r] = true;
        redraw(r, []).catch(function () {});
      });
    }).catch(function (err) { say(err.message, "bad"); });
  }
  function chatTo(ref, label) {
    var c = chatEl(), body = chatBody();
    if (!c || !body || !ref) return Promise.resolve();
    var pick = c.querySelector(".chat-pick select");
    if (pick) {
      var has = [].some.call(pick.options, function (o) { return o.value === ref; });
      if (!has) {
        var o = document.createElement("option");
        o.value = ref;
        o.textContent = label || ref;
        pick.insertBefore(o, pick.firstChild ? pick.firstChild.nextSibling : null);
      }
      pick.value = ref;
    }
    if (window.deskPref) window.deskPref.set("desk-chat-ref", ref);
    aboutStrip(pick && pick.selectedOptions ? pick.selectedOptions[0] : null, ref);
    if (body.dataset.ask === ref) return Promise.resolve();
    body.dataset.ask = ref;
    var t = body.querySelector(".ask-thread");
    if (t) t.innerHTML = '<p class="chat-empty">Loading…</p>';
    return thread(ref, true).then(function (html) {
      if (html === null) throw new Error("no thread");
      if (body.dataset.ask === ref) drawChat(html, []);
      chatScroll(true);
      [].forEach.call(body.querySelectorAll(".qa[data-live]"), function (li) {
        if (li.dataset.qa && !mine[li.dataset.qa]) mine[li.dataset.qa] = { ref: ref };
      });
      if (pending()) later(0);
    }).catch(function () {
      if (t) t.innerHTML = '<p class="chat-empty">Could not load that conversation.</p>';
    });
  }
  function chatOpen(on, focus) {
    var c = chatEl();
    if (!c) return;
    c.hidden = !on;
    if (window.deskPref) window.deskPref.set("desk-chat", on ? "1" : "0");
    if (!on) return;
    follow();
    chatScroll(true);
    var f = c.querySelector(".chat-box textarea");
    if (f && focus !== false) f.focus();
  }
  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest("[data-chat-toggle],[data-chat-close],[data-chat-open],.chat-new,[data-chat-more],[data-chat-clear]");
    if (!t) {
      if (!(e.target.closest && e.target.closest(".chat-menu"))) chatMenu(false);
      return;
    }
    var c = chatEl();
    if (!c) return;
    if (t.hasAttribute("data-chat-more")) {
      var m = c.querySelector(".chat-menu");
      chatMenu(m && m.hidden);
      return;
    }
    if (t.hasAttribute("data-chat-clear")) { chatClear(t.dataset.chatClear); return; }
    if (t.hasAttribute("data-chat-close")) { chatOpen(false); return; }
    if (t.hasAttribute("data-chat-toggle")) { chatOpen(c.hidden); return; }
    if (t.classList.contains("chat-new")) {
      var box = chatBox();
      if (box) { box.dataset.fresh = "1"; follow(); }
      var f = c.querySelector(".chat-box textarea");
      if (f) f.focus();
      return;
    }
    // From a card's composer: carry the words over, and the conversation with
    // them, rather than making the owner say again which job they mean.
    var card = t.closest(".ask");
    var typed = card && card.querySelector(".ask-box textarea");
    var words = typed ? typed.value : "";
    if (typed) typed.value = "";
    var shut = card && card.querySelector(".ask-cancel");
    if (shut) shut.click();
    var label = card && card.querySelector(".ask-of");
    chatTo(t.dataset.chatOpen, label ? label.textContent.trim() : "").then(function () {
      var f = c.querySelector(".chat-box textarea");
      if (f && words) f.value = words;
    });
    chatOpen(true);
  });
  document.addEventListener("change", function (e) {
    var pick = e.target.closest && e.target.closest(".chat-pick select");
    if (pick) chatTo(pick.value);
  });
  document.addEventListener("keydown", function (e) {
    var inBox = e.target.closest && e.target.closest(".chat-box");
    if (inBox && e.target.tagName === "TEXTAREA") {
      if (e.key === "Escape") { e.target.blur(); chatOpen(false); return; }
      if (e.key !== "Enter" || e.shiftKey || e.altKey || e.isComposing) return;
      e.preventDefault();
      var send = inBox.querySelector(".ask-send");
      if (send) send.click();
      return;
    }
    if (e.key !== "a" || e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
    var tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable) return;
    if (document.querySelector("dialog[open]")) return;
    var c = chatEl();
    if (!c) return;
    e.preventDefault();
    chatOpen(c.hidden);
  });
  // The week strip's arrows. The page always draws this week; another one is
  // fetched and swapped in. Which week the owner flipped to survives the page
  // reloading itself under them for ten minutes, so a sweep landing does not throw them back
  // to now mid-look, and after that, or in a new tab, it is this week again.
  function weekTo(offset, quiet) {
    var w = document.getElementById("week");
    if (!w || !w.classList) return;
    offset = Math.max(-52, Math.min(52, offset | 0));
    try {
      if (offset) sessionStorage.setItem("desk-week", JSON.stringify({ o: offset, at: Date.now() }));
      else sessionStorage.removeItem("desk-week");
    } catch (err) { /* private mode: it just will not remember */ }
    if (String(offset) === w.dataset.offset) return;
    w.classList.add("week-loading");
    fetch("/api/week?offset=" + offset).then(function (r) {
      if (!r.ok) throw new Error("the desk server would not draw that week");
      return r.text();
    }).then(function (html) {
      var cur = document.getElementById("week");
      if (!cur) return;
      var t = document.createElement("div");
      t.innerHTML = html.trim();
      if (t.firstChild) cur.replaceWith(t.firstChild);
      meetMark();
    }).catch(function (err) {
      w.classList.remove("week-loading");
      if (!quiet) say(err.message, "bad");
    });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-week]");
    if (!b) return;
    e.preventDefault();
    weekTo(parseInt(b.dataset.week, 10) || 0);
  });
  document.addEventListener("keydown", function (e) {
    if ((e.key !== "[" && e.key !== "]") || e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
    var tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable) return;
    if (document.querySelector("dialog[open]")) return;
    var w = document.getElementById("week");
    if (!w || !w.dataset) return;
    e.preventDefault();
    weekTo((parseInt(w.dataset.offset, 10) || 0) + (e.key === "]" ? 1 : -1));
  });
  function weekBack() {
    try {
      var kept = JSON.parse(sessionStorage.getItem("desk-week") || "null");
      if (kept && kept.o && Date.now() - kept.at < 600000) weekTo(kept.o, true);
    } catch (err) { /* nothing kept */ }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", weekBack);
  else weekBack();

  // The calendar. "Calendar 12m ago" reads it again now; the server does it in a
  // thread and this follows /api/status until it settles. A read that found the
  // meetings changed rewrites state/calendar.json, and the page reloads itself
  // from that like any other source, so nothing here redraws the week.
  var calWatch = null;
  function calFollow(b) {
    fetch("/api/status").then(function (r) { return r.json(); }).then(function (s) {
      var c = s.calendar || {};
      if (c.state === "running") return;
      clearInterval(calWatch); calWatch = null;
      if (b && b.removeAttribute) b.removeAttribute("aria-busy");
      if (c.state === "failed") say("Calendar: " + (c.said || "could not read it"), "bad");
      else if (b) b.textContent = "Calendar just now";
    }).catch(function () {
      clearInterval(calWatch); calWatch = null;
      if (b && b.removeAttribute) b.removeAttribute("aria-busy");
      say("lost the desk server", "bad");
    });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-cal-sync]");
    if (!b || b.getAttribute("aria-busy") === "true") return;
    b.setAttribute("aria-busy", "true");
    b.textContent = "Reading calendar";
    fetch("/api/calendar?k=" + encodeURIComponent(key), { method: "POST" }).then(function (r) {
      if (!r.ok) throw new Error("the desk server would not read the calendar");
      if (!calWatch) calWatch = setInterval(function () { calFollow(b); }, 2000);
    }).catch(function (err) {
      b.removeAttribute("aria-busy");
      b.textContent = "Calendar";
      say(err.message, "bad");
    });
  });
  // Set in Asana: the owner's own press, on one ticket, moving its Status to the
  // one the timeline says it should be. The server answers once Asana has, and
  // the board it writes reloads the page, so nothing here redraws the card.
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-asana-status]");
    if (!b || b.getAttribute("aria-busy") === "true") return;
    b.setAttribute("aria-busy", "true");
    b.textContent = "Setting";
    fetch("/api/asana-status?k=" + encodeURIComponent(key), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticket: b.getAttribute("data-asana-status") })
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, j: j }; });
    }).then(function (res) {
      if (!res.ok) throw new Error(res.j.error || "Asana did not take it");
      b.textContent = "Set";
      say("Asana Status is now " + res.j.now);
    }).catch(function (err) {
      b.removeAttribute("aria-busy");
      b.textContent = "Set in Asana";
      say(err.message || "lost the desk server", "bad");
    });
  });
  // Notes to fold in is a refresh: the sweep reads the Gemini notes the digest
  // names and puts what was said on the tickets. Same button, same run.
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-cal-fold]");
    if (!b) return;
    var r = document.getElementById("refresh");
    if (r && !r.disabled) r.click();
    else say("A refresh is already running; it reads the notes too.");
  });

  // Which meeting is on now and which is next, marked on the strip and offered
  // in the header as a Join chip from ten minutes before it starts. Re-marked
  // every half minute, so the page left open all day still knows the hour.
  var meets = [];
  try {
    var mj = document.getElementById("meets");
    meets = JSON.parse((mj && mj.textContent) || "[]") || [];
  } catch (err) { meets = []; }
  function meetMark() {
    if (!document.querySelectorAll) return;
    var now = Date.now();
    [].slice.call(document.querySelectorAll(".wm[data-start], .wd-room[data-start]")).forEach(function (el) {
      var s = Date.parse(el.dataset.start), en = Date.parse(el.dataset.end);
      if (isNaN(s)) return;
      el.classList.toggle("now", s <= now && now < en);
      el.classList.toggle("soon", s > now && s - now <= 15 * 60000);
    });
    var chip = document.getElementById("meet-now");
    if (!chip) return;
    var next = null;
    meets.forEach(function (m) {
      var s = Date.parse(m.s), en = Date.parse(m.e);
      if (isNaN(s) || now >= en || s - now > 10 * 60000) return;
      if (!next || (m.w && !next.w) || (m.w === next.w && s < Date.parse(next.s))) next = m;
    });
    if (!next || !safeUrl(next.j)) { chip.hidden = true; return; }
    var mins = Math.round((Date.parse(next.s) - now) / 60000);
    chip.href = safeUrl(next.j);
    // The time first, so the stylesheet's ellipsis eats the title and not the minutes.
    chip.textContent = (mins > 0 ? "Join in " + mins + "m: " : "Join now: ") + next.t;
    chip.title = next.t;
    chip.className = "meet-now" + (mins > 0 ? " later" : "");
    chip.hidden = false;
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", meetMark);
  else meetMark();
  setInterval(meetMark, 30000);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { follow(); chatScroll(true); });
  } else {
    follow();
  }

  // The status on every job, Jira's lozenge. Pressing it is the owner saying the
  // job moved, on the job they mean, so it runs ./tick.py like the terminal does and
  // the move is in the item's history. It always opens the moves open from where
  // the job stands, because a job here has more than two states and sending is
  // not finishing. Undo puts back exactly what was there.
  var menu = null, toastEl = null, toastTimer = null, last = null;
  function ticking() { return !!(menu || toastEl || inflight); }

  // What the lozenge says the moment a move is pressed, before the board answers.
  var AFTER = {
    done: ["Done", "green"], sent: ["Sent", "green"], dropped: ["Dropped", "grey"],
    mine: ["With you", "red"], waiting: ["Waiting", "blue"]
  };
  var TONE = ["red", "amber", "blue", "green", "grey"];

  function jobEls(n) {
    return [].slice.call(document.querySelectorAll('[data-job="' + n + '"]'));
  }
  function stats(n) {
    return [].slice.call(document.querySelectorAll('.stat[data-tick="' + n + '"]'));
  }
  // `look` is what the lozenge should say now, or null to put back what the
  // page was drawn with. Only a job that closed is struck through and folded
  // off the list; one handed to somebody is still open, just not theirs.
  function mark(n, closing, look) {
    jobEls(n).forEach(function (el) {
      el.classList.toggle("going", closing);
      if (!el.classList.contains("tr")) return;
      if (!closing) { fold(el, false); return; }
      setTimeout(function () { if (el.classList.contains("going")) fold(el, true); }, 650);
    });
    stats(n).forEach(function (c) {
      var t = c.querySelector(".stat-t");
      if (!c.dataset.was) c.dataset.was = t.textContent + "|" + (TONE.filter(function (k) { return c.classList.contains(k); })[0] || "grey");
      var was = c.dataset.was.split("|");
      var now = look || was;
      t.textContent = now[0];
      TONE.forEach(function (k) { c.classList.toggle(k, k === now[1]); });
      c.classList.toggle("on", !!look);
    });
    if (document.querySelectorAll) {
      [].slice.call(document.querySelectorAll('[data-week-job="' + n + '"]')).forEach(function (w) {
        w.hidden = !!closing;
        var col = w.closest && w.closest("li.wd");
        var badge = col && col.querySelector(".wd-c");
        if (badge && col.querySelectorAll) {
          var left = [].slice.call(col.querySelectorAll("[data-week-job]")).filter(function (x) { return !x.hidden; }).length;
          badge.textContent = left ? String(left) : "";
          badge.hidden = !left;
        }
      });
    }
  }
  // A row leaving the list folds up from its own height rather than vanishing,
  // so the rows under it slide up instead of jumping. Undo opens it the same way.
  function fold(el, shut) {
    if (shut === el.classList.contains("gone")) return;
    el.style.maxHeight = el.scrollHeight + "px";
    void el.offsetHeight;
    el.classList.toggle("gone", shut);
    if (shut) el.style.maxHeight = "0px";
    setTimeout(function () { if (!el.classList.contains("gone")) el.style.maxHeight = ""; }, 400);
  }
  function busy(n, on) {
    stats(n).forEach(function (c) { c.setAttribute("aria-busy", on ? "true" : "false"); });
  }

  function closeToast() {
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = null;
    if (toastEl) toastEl.remove();
    toastEl = null;
    later(0);
  }
  function toast(text, undo, bad) {
    if (toastEl) toastEl.remove();
    if (toastTimer) clearTimeout(toastTimer);
    toastEl = document.createElement("div");
    toastEl.className = "toast" + (bad ? " err" : "");
    toastEl.setAttribute("role", "status");
    var t = document.createElement("span");
    t.textContent = text;
    toastEl.appendChild(t);
    if (undo) {
      var u = document.createElement("button");
      u.type = "button";
      u.textContent = "Undo";
      u.title = "Put it back (u)";
      u.addEventListener("click", undo);
      toastEl.appendChild(u);
    }
    var x = document.createElement("button");
    x.type = "button";
    x.textContent = "×";
    x.setAttribute("aria-label", "Dismiss");
    x.addEventListener("click", closeToast);
    toastEl.appendChild(x);
    document.body.appendChild(toastEl);
    toastTimer = setTimeout(closeToast, undo ? 6000 : 3500);
  }

  // The page's own moves. While one is out the reload waits, and when it lands
  // the page takes the board's new stamp as what it was drawn from: it already
  // shows the move, and reloading to show it again was the flash that made a
  // press look like it had broken the page. A move made anywhere else still
  // reloads it, because that stamp was never handed to this page.
  var inflight = 0;
  function adopt(j) {
    if (!j || !j.stamp) return;
    drawnFrom = j.stamp;
    if (j.board) drawnBoard = j.board;
  }
  function tick(n, verb, who, before, extra) {
    var body = { n: n, verb: verb, who: who || "", before: before || null };
    for (var f in extra || {}) if (Object.hasOwn(extra, f)) body[f] = extra[f];
    inflight += 1;
    return fetch("/api/tick?k=" + encodeURIComponent(key), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        inflight -= 1;
        if (!r.ok) throw new Error(j.error || "the desk server said no");
        adopt(j);
        return j;
      });
    }, function (err) { inflight -= 1; throw err; });
  }

  // The closing wave used to fire on the reload after a close. There is no
  // reload now, so it fires here, and the page's record of what was closed
  // moves with it so the next real reload does not play it a second time.
  function closed(n, on) {
    if (window.deskWave) window.deskWave(n, on);
  }

  // A step is a line of the owner's checklist inside a job. Ticking one strikes
  // it through, moves the highlight on, and leaves the job where it is.
  function stepPaint(list) {
    var lis = [].slice.call(list.querySelectorAll("li.ds"));
    var first = true, ticked = 0;
    lis.forEach(function (li) {
      var on = li.classList.contains("done");
      ticked += on ? 1 : 0;
      li.classList.toggle("next", !on && first);
      if (!on) first = false;
      var b = li.querySelector(".ds-n");
      if (!b) return;
      var said = on ? "Step " + b.dataset.step + " done, press to put it back" : "Mark step " + b.dataset.step + " done";
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.setAttribute("aria-label", said);
      b.title = said;
    });
    var wrap = list.closest(".act-do");
    var c = wrap && wrap.querySelector(".steps-count");
    if (!c) return;
    c.textContent = ticked + " of " + lis.length + " done";
    c.toggleAttribute("data-none", !ticked);
    c.classList.toggle("all", ticked === lis.length);
  }
  function stepMark(li, list, on) {
    li.classList.toggle("done", on);
    li.classList.remove("pop");
    if (on) {
      void li.offsetWidth;
      li.classList.add("pop");
      setTimeout(function () { li.classList.remove("pop"); }, 420);
    }
    stepPaint(list);
  }
  // Posting a draft ticks the step that said to post it, on the server. The
  // page does not reload for its own move, so it strikes those steps here.
  function stepsPosted(n, ks, on) {
    (ks || []).forEach(function (k) {
      var b = document.querySelector('.ds-n[data-step-job="' + n + '"][data-step="' + k + '"]');
      var li = b && b.closest("li.ds"), list = b && b.closest("ol.steps");
      if (li && list) stepMark(li, list, on);
    });
  }
  function setStep(b, on, offerUndo) {
    var li = b.closest("li.ds"), list = b.closest("ol.steps");
    var n = b.dataset.stepJob, k = b.dataset.step;
    if (!li || !list) return;
    stepMark(li, list, on);
    b.setAttribute("aria-busy", "true");
    tick(n, "step", "", null, { step: k, off: !on }).then(function (j) {
      b.removeAttribute("aria-busy");
      if (j && j.closed) {
        mark(n, true, AFTER.done);
        closed(n, true);
        if (offerUndo) last = { n: n, before: j.before, closing: true, look: AFTER.done, step: b, on: on };
      } else if (offerUndo) {
        last = { n: n, step: b, on: on };
      }
      toast(j.said || "Step " + k + " on " + n + (on ? " done" : " back to do"), offerUndo ? undoLast : null);
    }).catch(function (err) {
      b.removeAttribute("aria-busy");
      li.classList.toggle("done", !on);
      stepPaint(list);
      toast("Could not tick step " + k + " on " + n + ": " + err.message, null, true);
    });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest(".ds-n");
    if (!b) return;
    e.preventDefault();
    cancelOutbound();
    if (b.getAttribute("aria-busy") === "true") return;
    var li = b.closest("li.ds");
    setStep(b, !(li && li.classList.contains("done")), true);
  });

  function undoLast() {
    if (!last) return;
    var was = last;
    last = null;
    if (was.step && !was.closing) { setStep(was.step, !was.on, false); return; }
    if (was.step && was.closing) {
      var sLi = was.step.closest && was.step.closest("li.ds");
      var sList = was.step.closest && was.step.closest("ol.steps");
      if (sLi && sList) stepMark(sLi, sList, false);
    }
    if (was.closing) closed(was.n, false);
    mark(was.n, false, null);
    busy(was.n, true);
    toast("Putting " + was.n + " back…");
    if (was.draft) { was.draft.classList.remove("posted", "nudge"); was.draft.open = true; was.btn.disabled = false; }
    stepsPosted(was.n, was.steps, false);
    tick(was.n, "undo", "", was.before).then(function () {
      busy(was.n, false);
      toast(was.n + " is back where it was");
    }).catch(function (err) {
      busy(was.n, false);
      mark(was.n, was.closing, was.look);
      stepsPosted(was.n, was.steps, true);
      toast("Could not undo " + was.n + ": " + err.message, null, true);
    });
  }

  function move(n, verb, who, shown) {
    closeMenu();
    var closing = verb === "done" || verb === "sent" || verb === "dropped";
    var look = verb === "waiting" ? ["Waiting on " + (shown || who), "blue"] : AFTER[verb];
    mark(n, closing, look);
    busy(n, true);
    tick(n, verb, who).then(function (j) {
      busy(n, false);
      if (verb === "done" || verb === "sent") closed(n, true);
      else if (closing) { if (window.deskWave) window.deskWave(n, false); }
      else if (verb === "waiting" && window.deskWave && window.deskWave.sent) {
        window.deskWave.sent(n, shown || who || "");
      }
      last = { n: n, before: j.before, closing: closing, look: look };
      toast(j.said || n + " moved", undoLast);
    }).catch(function (err) {
      busy(n, false);
      mark(n, false, null);
      toast("Could not move " + n + ": " + err.message, null, true);
    });
  }

  function closeMenu() {
    if (!menu) return;
    var from = menu._from;
    menu.remove();
    menu = null;
    if (from) from.setAttribute("aria-expanded", "false");
  }
  function option(label, small, fn) {
    var b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role", "menuitem");
    b.textContent = label;
    if (small) {
      var s = document.createElement("small");
      s.textContent = small;
      b.appendChild(s);
    }
    b.addEventListener("click", function (e) { e.stopPropagation(); fn(); });
    return b;
  }
  // Who the board knows, worked out by people.py from every name it holds and
  // embedded in the page, so picking someone is picking a person rather than
  // typing "Sato-san" and hoping the next sweep reads it the same way.
  var PEOPLE = null;
  function directory() {
    if (PEOPLE) return PEOPLE;
    PEOPLE = { people: [], near: {}, byId: {} };
    var el = document.getElementById("desk-people");
    try {
      var j = el ? JSON.parse(el.textContent) : null;
      if (j && j.people) { PEOPLE.people = j.people; PEOPLE.near = j.near || {}; }
    } catch (err) { /* no directory: the picker still takes a typed name */ }
    PEOPLE.people.forEach(function (p) { PEOPLE.byId[p.id] = p; });
    return PEOPLE;
  }
  function avatarEl(p) {
    var a = document.createElement("span");
    a.className = "av" + (p.team ? " team" : "");
    a.setAttribute("aria-hidden", "true");
    a.textContent = p.ini || "?";
    if (p.hue !== undefined) a.style.setProperty("--h", p.hue);
    return a;
  }
  // The picker: chips for who it is with, a search box, and the people to
  // pick from. The draft's addressee comes in already chosen, so x then Enter
  // is still the whole of the common case. Enter or a click picks and moves the
  // job; Shift+Enter or + adds a second person first. Anyone not on the board
  // yet is one "Use" row away, and joins the directory once it is written.
  function personPicker(n, button, preIds, preText, pick) {
    var dir = directory();
    var chosen = [];
    (preIds || []).forEach(function (id) { if (dir.byId[id]) chosen.push(dir.byId[id]); });
    if (!chosen.length && preText) chosen.push({ id: "x-" + preText, name: preText, short: preText, value: preText, ini: preText.slice(0, 2).toUpperCase(), custom: true });
    var wrap = document.createElement("div");
    wrap.className = "who-pick";
    var field = document.createElement("div");
    field.className = "wp-field";
    var input = document.createElement("input");
    input.placeholder = "Search people or teams";
    input.setAttribute("aria-label", "Who has it now?");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("autocomplete", "off");
    var list = document.createElement("div");
    list.className = "wp-list";
    list.setAttribute("role", "listbox");
    var foot = document.createElement("div");
    foot.className = "wp-foot";
    var hint = document.createElement("span");
    hint.className = "wp-hint";
    var go = document.createElement("button");
    go.type = "button";
    go.className = "wp-go";
    go.textContent = button;
    foot.appendChild(hint);
    foot.appendChild(go);
    wrap.appendChild(field);
    wrap.appendChild(list);
    wrap.appendChild(foot);
    var rows = [], at = -1;

    function value() {
      var full = chosen.map(function (p) { return p.value; }).join(" and ");
      if (full.length <= 80) return full;
      return chosen.map(function (p) { return p.name; }).join(" and ").slice(0, 80);
    }
    function said() {
      var s = chosen.map(function (p) { return p.short || p.name; });
      return s.length < 2 ? (s[0] || "") : s.slice(0, -1).join(", ") + " and " + s[s.length - 1];
    }
    function submit(extra) {
      if (extra && !chosen.some(function (p) { return p.id === extra.id; })) chosen.push(extra);
      if (!chosen.length) { input.focus(); return; }
      (pick || function (v, w) { move(n, "waiting", v, w); })(value(), said());
    }
    function add(p) {
      if (!chosen.some(function (c) { return c.id === p.id; })) chosen.push(p);
      input.value = "";
      draw();
      input.focus();
    }
    function chips() {
      [].slice.call(field.querySelectorAll(".wp-chip")).forEach(function (c) { c.remove(); });
      chosen.forEach(function (p, i) {
        var c = document.createElement("span");
        c.className = "wp-chip";
        c.appendChild(avatarEl(p));
        var t = document.createElement("span");
        t.textContent = p.short || p.name;
        c.appendChild(t);
        var x = document.createElement("button");
        x.type = "button";
        x.textContent = "×";
        x.setAttribute("aria-label", "Remove " + (p.short || p.name));
        x.addEventListener("click", function (e) {
          e.stopPropagation();
          chosen.splice(i, 1);
          draw();
          input.focus();
        });
        c.appendChild(x);
        field.insertBefore(c, input);
      });
      go.disabled = !chosen.length;
      go.textContent = chosen.length ? button + " · " + said() : button;
    }
    function score(p, q) {
      var hay = [p.name, p.short, p.ja, p.org].concat(p.also || []).join(" ").toLowerCase();
      if (hay.indexOf(q) < 0) return -1;
      var words = (p.name + " " + p.short).toLowerCase().split(/[\s,-]+/);
      var start = words.some(function (w) { return w.indexOf(q) === 0; });
      return (start ? 100 : 0) + Math.min(p.n || 0, 99);
    }
    function head(text) {
      var h = document.createElement("div");
      h.className = "wp-h";
      h.textContent = text;
      list.appendChild(h);
    }
    function row(p, label) {
      var r = document.createElement("div");
      r.className = "wp-row";
      r.setAttribute("role", "option");
      r.id = "wp-" + n + "-" + rows.length;
      var picked = chosen.some(function (c) { return c.id === p.id; });
      if (picked) r.classList.add("picked");
      r.appendChild(avatarEl(p));
      var t = document.createElement("span");
      t.className = "wp-t";
      var b = document.createElement("b");
      b.textContent = label || p.name;
      t.appendChild(b);
      var sub = [p.team ? "Team" : p.org, p.ja].filter(Boolean).join(" · ");
      if (sub) {
        var s = document.createElement("small");
        s.textContent = sub;
        t.appendChild(s);
      }
      r.appendChild(t);
      if (!p.custom || label) {
        var plus = document.createElement("button");
        plus.type = "button";
        plus.className = "wp-add";
        plus.textContent = picked ? "✓" : "+";
        plus.title = picked ? "Already with them" : "Add, and pick another (Shift+Enter)";
        plus.addEventListener("click", function (e) { e.stopPropagation(); if (!picked) add(p); });
        r.appendChild(plus);
      }
      r.addEventListener("mousedown", function (e) { e.preventDefault(); });
      r.addEventListener("click", function () { submit(p); });
      r._p = p;
      rows.push(r);
      list.appendChild(r);
    }
    function draw() {
      chips();
      list.innerHTML = "";
      rows = [];
      var q = input.value.trim().toLowerCase();
      var seen = {};
      chosen.forEach(function (p) { seen[p.id] = true; });
      if (q) {
        var hits = dir.people.map(function (p) { return [score(p, q), p]; })
          .filter(function (x) { return x[0] >= 0 && !seen[x[1].id]; })
          .sort(function (a, b) { return b[0] - a[0]; }).slice(0, 7);
        hits.forEach(function (x) { row(x[1]); });
        var typed = input.value.trim().slice(0, 60);
        row({ id: "x-" + typed, name: typed, short: typed, value: typed, ini: typed.slice(0, 2).toUpperCase(), custom: true },
            "Use “" + typed + "”");
        at = 0;
      } else {
        var sugg = (PEOPLE.near[n] || []).map(function (id) { return dir.byId[id]; })
          .filter(function (p) { return p && !seen[p.id]; }).slice(0, 5);
        if (sugg.length) { head("On this ticket"); sugg.forEach(function (p) { seen[p.id] = true; row(p); }); }
        var often = dir.people.filter(function (p) { return !p.team && !seen[p.id]; }).slice(0, 5);
        if (often.length) { head("Often"); often.forEach(function (p) { seen[p.id] = true; row(p); }); }
        var teams = dir.people.filter(function (p) { return p.team && !seen[p.id]; });
        if (teams.length) { head("Teams"); teams.forEach(function (p) { row(p); }); }
        at = chosen.length ? -1 : 0;
      }
      hint.textContent = q ? "Enter picks · Shift+Enter adds another"
        : chosen.length ? "Enter to move it" : "Type a name, or pick one";
      mark();
    }
    function mark() {
      rows.forEach(function (r, i) { r.classList.toggle("on", i === at); });
      if (rows[at]) {
        input.setAttribute("aria-activedescendant", rows[at].id);
        rows[at].scrollIntoView({ block: "nearest" });
      } else input.removeAttribute("aria-activedescendant");
    }
    input.addEventListener("input", draw);
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        e.stopPropagation();
        if (!rows.length) return;
        at = e.key === "ArrowDown" ? Math.min(rows.length - 1, at + 1) : Math.max(-1, at - 1);
        mark();
      } else if (e.key === "Enter") {
        e.preventDefault();
        var p = rows[at] && rows[at]._p;
        if (e.shiftKey || e.metaKey) { if (p) add(p); return; }
        submit(p || null);
      } else if (e.key === "Backspace" && !input.value && chosen.length) {
        chosen.pop();
        draw();
      }
    });
    go.addEventListener("click", function (e) { e.stopPropagation(); submit(null); });
    field.appendChild(input);
    field.addEventListener("click", function () { input.focus(); });
    draw();
    return wrap;
  }
  function heading(text) {
    var h = document.createElement("div");
    h.className = "stat-h";
    h.textContent = text;
    return h;
  }
  function openMenu(c) {
    closeMenu();
    if (c.getAttribute("aria-busy") === "true") return;
    var n = c.dataset.tick, st = c.dataset.state, who = c.dataset.who || "";
    menu = document.createElement("div");
    menu.className = "stat-menu";
    menu.setAttribute("role", "menu");
    menu._from = c;
    c.setAttribute("aria-expanded", "true");
    var title = c.dataset.title || "";
    menu.appendChild(heading(n + (title ? "  " + title : "")));
    if (st === "done" || st === "sent" || st === "dropped") {
      menu.appendChild(option("Open it again", "back with you", function () { move(n, "mine"); }));
    } else if (st === "waiting") {
      menu.appendChild(option("They replied", "it is yours again", function () { move(n, "mine"); }));
      menu.appendChild(option("Done", "nothing more comes back", function () { move(n, "done"); }));
      menu.appendChild(heading("Handed on to"));
      menu.appendChild(personPicker(n, "Move", [], ""));
      menu.appendChild(option("Dropped", "no longer needed", function () { move(n, "dropped"); }));
    } else {
      // First, because it is the move the owner makes most: sent it, a reply is owed.
      // The name comes in filled from the draft, so x then Enter is the whole
      // of it, and the next sweep looks for the message and for the answer.
      menu.appendChild(heading("Sent, waiting on"));
      var ids = (c.dataset.whoids || c.dataset.toids || "").split(",").filter(Boolean);
      menu.appendChild(personPicker(n, "Sent", ids, ids.length ? "" : (who || c.dataset.to || "")));
      if (st === "hold") {
        menu.appendChild(option("Start it now", "the hold is over, it is yours", function () { move(n, "mine"); }));
      }
      menu.appendChild(option("Done", "nothing comes back", function () { move(n, "done"); }));
      menu.appendChild(option("Dropped", "no longer needed", function () { move(n, "dropped"); }));
    }
    place(c);
  }
  function place(c) {
    document.body.appendChild(menu);
    var r = c.getBoundingClientRect();
    var left = Math.min(r.right + window.scrollX - menu.offsetWidth, window.scrollX + document.documentElement.clientWidth - menu.offsetWidth - 12);
    menu.style.left = Math.max(8, left) + "px";
    // Below when it fits, else whichever side has more room, and never up under
    // the sticky header where its first rows cannot be seen.
    var bar = 58, room = window.innerHeight - r.bottom - 6, up = r.top - bar - 6;
    var below = menu.offsetHeight <= room || room >= up;
    menu.style.top = (below ? r.bottom + window.scrollY + 6
      : Math.max(window.scrollY + bar, r.top + window.scrollY - menu.offsetHeight - 6)) + "px";
    var first = menu.querySelector("input,[role=menuitem]");
    if (first) first.focus();
  }

  // Posted it, at the foot of each draft. The draft is where the owner copies the
  // words from, so it is where they come back to say they went: copy, paste,
  // press. It used to be a step reading "Then ./tick.py <n> once it is posted".
  // The button already says what it will do (wait on Sato-san, close the
  // job, or one more to send) and does it in one press, with the same Undo as
  // the status. The arrow beside it is for when the guess is wrong: someone
  // else replies, or nothing comes back at all.
  // This script is inlined above the cards, so the bars are not there yet.
  function showFeet() {
    [].forEach.call(document.querySelectorAll(".draft-foot"), function (f) { f.hidden = false; });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", showFeet);
  else showFeet();
  function box(b) { return b.closest(".act-draft"); }
  function postedLook(b, who, close) {
    var after = close ? "close" : who ? "wait" : b.dataset.after;
    if (after === "more") return null;
    if (after === "close") return ["Sent", "green"];
    return ["Waiting on " + (who || b.dataset.who || "them"), "blue"];
  }
  function postIt(b, who, shown, close) {
    closeMenu();
    var n = b.dataset.posted, d = box(b);
    var look = postedLook(b, shown || who, close);
    var closing = !!(look && look[1] === "green");
    if (d) { d.classList.add("posting"); d.open = false; }
    if (look) mark(n, closing, look);
    busy(n, true);
    b.disabled = true;
    tick(n, "posted", who, null, { draft: b.dataset.draft, close: !!close }).then(function (j) {
      busy(n, false);
      if (d) { d.classList.remove("posting"); d.classList.add("posted"); }
      if (closing) closed(n, true);
      else if (look && look[1] === "blue" && window.deskWave && window.deskWave.sent) {
        window.deskWave.sent(n, shown || who || b.dataset.who || "");
      }
      stepsPosted(n, j.steps, true);
      last = { n: n, before: j.before, closing: closing, look: look, draft: d, btn: b, steps: j.steps };
      toast(j.said || "Posted", undoLast);
    }).catch(function (err) {
      busy(n, false);
      b.disabled = false;
      if (d) { d.classList.remove("posting"); d.open = true; }
      if (look) mark(n, false, null);
      toast("Could not mark it posted: " + err.message, null, true);
    });
  }
  function postedMenu(b) {
    closeMenu();
    var n = b.dataset.posted;
    menu = document.createElement("div");
    menu.className = "stat-menu";
    menu.setAttribute("role", "menu");
    menu._from = b;
    b.setAttribute("aria-expanded", "true");
    menu.appendChild(heading("Posted, waiting on"));
    var ids = (b.dataset.whoids || "").split(",").filter(Boolean);
    menu.appendChild(personPicker(n, "Posted", ids, ids.length ? "" : (b.dataset.who || ""),
      function (v, w) { postIt(b, v, w, false); }));
    menu.appendChild(option("Posted, nothing comes back", "closes the job as sent",
      function () { postIt(b, "", "", true); }));
    place(b);
  }
  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest(".posted,.posted-more");
    if (!t) return;
    e.preventDefault();
    cancelOutbound();
    // Immediate, or the status menu's own capture listener below sees a click
    // outside every .stat and shuts the menu this just opened.
    e.stopImmediatePropagation();
    var b = t.classList.contains("posted") ? t : t.parentNode.querySelector(".posted");
    if (!b || b.disabled) return;
    if (menu && menu._from === b) { closeMenu(); return; }
    if (t !== b || b.dataset.after === "ask") postedMenu(b);
    else postIt(b, "", "", false);
  }, true);
  // Copying the words, or following an Asana/Slack link to where they go, is the
  // moment the owner is about to post or act externally. Light the bar under a
  // draft, and remember the outbound timestamp so returning to DANDORI checks
  // automatically whether the post or edit moved the board.
  document.addEventListener("click", function (e) {
    var ext = e.target.closest && e.target.closest('a[href*="asana.com"],a[href*="slack.com"]');
    if (ext) outboundAt = Date.now();
    var c = e.target.closest && e.target.closest(".act-draft.live .copy, .act-draft.live .draft-head a");
    if (!c) return;
    outboundAt = Date.now();
    var d = box(c);
    [].forEach.call(document.querySelectorAll(".act-draft.nudge"), function (x) {
      if (x !== d) x.classList.remove("nudge");
    });
    if (d && d.querySelector(".draft-foot:not([hidden])")) d.classList.add("nudge");
  });

  // Up and down walk the menu, the way every menu does, and Escape leaves it
  // from anywhere in it, the name box included.
  document.addEventListener("keydown", function (e) {
    if (menu && e.key === "Escape") {
      var from = menu._from;
      closeMenu();
      if (from) from.focus();
      e.preventDefault();
      return;
    }
    if (!menu || (e.key !== "ArrowDown" && e.key !== "ArrowUp")) return;
    var all = [].slice.call(menu.querySelectorAll("input,[role=menuitem]"));
    var i = all.indexOf(document.activeElement);
    var next = all[(i + (e.key === "ArrowDown" ? 1 : all.length - 1)) % all.length];
    if (next) { next.focus(); e.preventDefault(); }
  });

  // Capture, so the press never reaches the summary it sits in and opens the
  // card instead.
  document.addEventListener("click", function (e) {
    var c = e.target.closest && e.target.closest(".stat");
    if (!c) {
      if (menu && !(e.target.closest && e.target.closest(".stat-menu"))) closeMenu();
      return;
    }
    e.preventDefault();
    e.stopPropagation();
    cancelOutbound();
    if (menu && menu._from === c) closeMenu();
    else openMenu(c);
  }, true);

  // j and k walk the list, x opens its status, Enter opens the card, u undoes.
  // The list is the To do list on the desk; a row folded away is not in the walk.
  var focusAt = -1;
  function walk() {
    var list = document.querySelector("#todo .track-list");
    if (!list) return [];
    return [].slice.call(list.querySelectorAll("[data-job]")).filter(function (el) {
      return el.offsetParent !== null && !el.classList.contains("going");
    });
  }
  function focusRow(rows, i) {
    rows.forEach(function (el) { el.classList.remove("kfocus"); });
    if (!rows.length) return;
    focusAt = Math.max(0, Math.min(i, rows.length - 1));
    rows[focusAt].classList.add("kfocus");
    rows[focusAt].scrollIntoView({ block: "nearest" });
  }
  document.addEventListener("keydown", function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
    var tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || e.target.isContentEditable) return;
    if (document.querySelector("dialog[open]")) return;
    if (document.body.dataset.view === "standup") return;
    var rows = walk();
    var cur = document.querySelector("#todo .kfocus");
    var at = cur ? rows.indexOf(cur) : -1;
    if (e.key === "j") { focusRow(rows, at + 1); e.preventDefault(); }
    else if (e.key === "k") { focusRow(rows, at < 0 ? 0 : at - 1); e.preventDefault(); }
    else if ((e.key === "x" || e.key === "e") && cur) {
      var c = cur.querySelector(".stat");
      if (c) { openMenu(c); e.preventDefault(); }
    } else if ((e.key === "Enter" || e.key === "o") && cur) {
      var a = cur.querySelector("a.tr-title,a[href]");
      if (a) { a.click(); e.preventDefault(); }
    } else if (e.key === "u" && last) { undoLast(); e.preventDefault(); }
  });

  // Coming back to the tab is the moment worth checking. The usual way the board
  // moves is the owner running `./tick.py <n>` in a terminal or posting a draft
  // in Slack/Asana and then looking back at the page.
  function onReturn() {
    if (document.hidden) {
      if (scoutWait) { clearTimeout(scoutWait); scoutWait = null; }
      return;
    }
    later(0);
    if (autoMins <= 0) return;
    if (outboundAt > 0) {
      var elapsed = Date.now() - outboundAt;
      var waitMs = elapsed >= 20000 ? 1500 : (20000 - elapsed);
      if (scoutWait) clearTimeout(scoutWait);
      scoutWait = setTimeout(function () {
        scoutWait = null;
        if (document.hidden || !outboundAt) return;
        outboundAt = 0;
        triggerScout();
      }, waitMs);
      return;
    }
    if (lastCheckedEpoch > 0 && (Date.now() / 1000 - lastCheckedEpoch) >= autoMins * 60) {
      var d = new Date(), wd = d.getDay(), hr = d.getHours();
      if (wd >= 1 && wd <= 5 && hr >= 8 && hr < 20) triggerScout();
    }
  }
  document.addEventListener("visibilitychange", onReturn);
  window.addEventListener("focus", function () { if (!document.hidden) onReturn(); });

  poll();
})();
