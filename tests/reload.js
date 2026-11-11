// The page reloads itself when the board moves under it. This is the test that
// it never does so while the owner is part-way through typing a question.
//
// Losing a half-written question to a helpful reload would be worse than the
// staleness the reload fixes, and it is the kind of fault you only find by doing
// it to yourself at the wrong moment. So the guard gets a test, run by
// ./check.py, with no browser and no packages: a stub DOM, a stub fetch, and the
// real static/desk.js evaluated against them.
//
//     node tests/reload.js

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");

function element(extra) {
  return Object.assign(
    {
      dataset: {},
      hidden: false,
      disabled: false,
      open: false,
      value: "",
      textContent: "",
      className: "",
      classList: {
        add() {}, remove() {}, toggle() {}, contains() { return false; },
      },
      listeners: {},
      addEventListener(type, fn) { this.listeners[type] = fn; },
      removeEventListener() {},
      appendChild() {}, removeAttribute() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
      closest() { return null; },
      setAttribute() {}, getAttribute() { return null; },
      scrollIntoView() {}, focus() {}, remove() {},
      parentNode: null,
    },
    extra
  );
}

// One page, with the knobs the tests turn: what the page was drawn from, what is
// typed into the composer, and whether a composer is open at all.
function makePage(options) {
  const opts = Object.assign(
    { stamp: "AAA", typed: "", composerOpen: false, palOpen: false, helpOpen: false,
      syncOpen: false, running: "" },
    options
  );

  const ids = {
    login: element(),
    "login-link": element(),
    "refresh-note": element(),
    "sync-btn": element({ hidden: true }),
    "sync-dot": element(),
    "sync-pop": element({ hidden: !opts.syncOpen }),
    "scout-now": element(),
    "sp-runs": element(),
    changed: element({ hidden: true }),
    pal: element({ hidden: !opts.palOpen }),
    help: element({ open: opts.helpOpen }),
  };
  const textarea = element({ value: opts.typed });

  const reloads = { count: 0 };
  const timers = [];

  const document = {
    body: element({ dataset: { stamp: opts.stamp, board: "B1" } }),
    hidden: false,
    getElementById: (id) => (Object.hasOwn(ids, id) ? ids[id] : null),
    querySelectorAll: (sel) => {
      if (sel === "[data-run]") return [element({ dataset: {} })];
      if (sel === ".ask textarea") return [textarea];
      // A question still out when the page was drawn: a bubble on one card.
      if (sel === ".qa[data-live]" && opts.running) {
        const box = element({ dataset: { ask: "item:1" } });
        return [element({ dataset: { qa: opts.running }, closest: () => box })];
      }
      return [];
    },
    querySelector: (sel) => {
      if (sel.indexOf(".ask-box") === 0) return opts.composerOpen ? element() : null;
      return null;
    },
    // Kept, so a test can press something the way a click would arrive.
    clicks: [],
    visListeners: [],
    addEventListener(type, fn) {
      if (type === "click") this.clicks.push(fn);
      if (type === "visibilitychange") this.visListeners.push(fn);
    },
    createElement: () => element(),
  };

  const winListeners = [];
  const sandbox = {
    document,
    window: {
      focusListeners: winListeners,
      addEventListener(type, fn) { if (type === "focus") winListeners.push(fn); },
    },
    location: { protocol: "http:", reload: () => { reloads.count += 1; } },
    navigator: {},
    console,
    JSON,
    Math,
    encodeURIComponent,
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: () => {},
    // The meeting marks re-draw on an interval. Not the reload's business, so
    // they never run here and never count as the page watching.
    setInterval: () => 0,
    clearInterval: () => {},
    // Whatever the test wants the server to be saying this poll.
    fetch: (url) => {
      sandbox.fetched.push(url);
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(sandbox.reply),
        text: () => Promise.resolve("<ul class=\"qa-list\"></ul>"),
      });
    },
    fetched: [],
    Object,
    Date,
    reply: { state: "idle", message: "", asks: {}, stamp: opts.stamp },
  };

  const source = fs
    .readFileSync(path.join(ROOT, "core", "static", "desk.js"), "utf8")
    // serve.py does this as it serves. Without it the script returns at line 3,
    // which is the whole point of that line, and the test would pass on nothing.
    .replace("__DESK_KEY__", "test-key-not-a-real-one")
    .replace("__DESK_BOOT__", "boot1");

  vm.runInNewContext(source, sandbox, { filename: "core/static/desk.js" });
  return { sandbox, reloads, timers, ids, textarea };
}

// The script polls on load and the reply is a promise, so let the microtasks run.
const settle = () => new Promise((r) => setImmediate(r));

const results = [];
function check(name, pass, detail) {
  results.push({ name, pass, detail });
}

async function run() {
  // 1. Nothing has changed. The page must sit quiet and keep watching.
  {
    const page = makePage({ stamp: "AAA" });
    page.sandbox.reply = { state: "idle", message: "", asks: {}, stamp: "AAA" };
    await settle();
    check("same stamp does not reload", page.reloads.count === 0,
      `reloaded ${page.reloads.count} times`);
    check("same stamp keeps watching", page.timers.length > 0,
      "no follow-up poll was scheduled, so the page went blind");
  }

  // 2. The board moved and nothing is half-written. This is `./tick.py 26` in a
  //    terminal, and it should reload on its own.
  {
    const page = makePage({ stamp: "AAA" });
    page.sandbox.reply = { state: "idle", message: "", asks: {}, stamp: "BBB" };
    await settle();
    check("a moved board reloads a clean page", page.reloads.count === 1,
      `reloaded ${page.reloads.count} times, wanted once`);
  }

  // 3. The board moved while the owner is typing. Nothing may be taken from under them.
  {
    const page = makePage({ stamp: "AAA", typed: "why is this still with Alex" });
    page.sandbox.reply = { state: "idle", message: "", asks: {}, stamp: "BBB" };
    await settle();
    check("typing is never reloaded away", page.reloads.count === 0,
      "it reloaded and took their half-written question with it");
    check("it offers the reload instead", page.ids.changed.hidden === false,
      "no way back: it neither reloaded nor showed the button");
  }

  // 4. A composer open but empty still counts as theirs: reloading closes it.
  {
    const page = makePage({ stamp: "AAA", composerOpen: true });
    page.sandbox.reply = { state: "idle", message: "", asks: {}, stamp: "BBB" };
    await settle();
    check("an open composer is left alone", page.reloads.count === 0,
      "it closed a composer they had opened");
  }

  // 5. The finder open over the page is the same story.
  {
    const page = makePage({ stamp: "AAA", palOpen: true });
    page.sandbox.reply = { state: "idle", message: "", asks: {}, stamp: "BBB" };
    await settle();
    check("the finder is not reloaded away", page.reloads.count === 0,
      "it reloaded while the finder was open");
  }

  // 6. A server that never sends a stamp at all, which is an older serve.py
  //    still running behind a freshly rendered page.
  {
    const page = makePage({ stamp: "AAA" });
    page.sandbox.reply = { state: "idle", message: "", asks: {} };
    await settle();
    check("no stamp from the server is not a change", page.reloads.count === 0,
      "a server with no stamp put the page in a reload loop");
  }

  // 7. The server restarted under an unchanged board. Its key is new, so every
  //    button on this page would be refused until it reloads.
  {
    const page = makePage({ stamp: "AAA" });
    page.sandbox.reply = { state: "idle", message: "", asks: {}, stamp: "AAA", boot: "boot2" };
    await settle();
    check("a restarted server reloads a clean page", page.reloads.count === 1,
      `reloaded ${page.reloads.count} times, wanted once`);
  }

  // 8. The same server, same board: still quiet.
  {
    const page = makePage({ stamp: "AAA" });
    page.sandbox.reply = { state: "idle", message: "", asks: {}, stamp: "AAA", boot: "boot1" };
    await settle();
    check("the same server is not a restart", page.reloads.count === 0,
      "it reloaded with nothing changed");
  }

  // 9. Their question was answered and the answer touched nothing but the
  //    conversation. It goes into the card in place; a reload is what used to
  //    put them somewhere else on the board.
  {
    const page = makePage({ stamp: "AAA", running: "q1" });
    page.sandbox.reply = { state: "done", message: "", asks: {}, stamp: "BBB", board: "B1" };
    await settle();
    await settle();
    check("an answer lands without a reload", page.reloads.count === 0,
      `reloaded ${page.reloads.count} times, wanted none`);
    check("it redraws that card's thread", page.sandbox.fetched.some((u) => u.indexOf("/api/thread?ref=item%3A1") === 0),
      "never asked the server for the card's thread: " + page.sandbox.fetched.join(", "));
  }

  // 10. The same answer, but it rewrote the board too (a draft, an item). That
  //     card must be redrawn from the board, so this one does reload.
  {
    const page = makePage({ stamp: "AAA", running: "q1" });
    page.sandbox.reply = { state: "done", message: "", asks: {}, stamp: "BBB", board: "B2" };
    await settle();
    await settle();
    check("an answer that moved the board reloads", page.reloads.count === 1,
      `reloaded ${page.reloads.count} times, wanted once`);
  }

  // 11. The owner ticks a step on the page. The page already shows it, so the
  //     stamp that move produced is adopted and the next poll does not reload.
  //     A reload after every press is the flash that looked like a fault.
  {
    const page = makePage({ stamp: "AAA" });
    await settle();
    const before = page.reloads.count;
    const on = new Set();
    const li = element({ classList: {
      add: (c) => on.add(c), remove: (c) => on.delete(c), contains: (c) => on.has(c),
      toggle: (c, f) => { if (f === undefined ? !on.has(c) : f) on.add(c); else on.delete(c); },
    } });
    const list = element({ querySelectorAll: () => [li] });
    const btn = element({ dataset: { stepJob: "59", step: "1" },
      closest: (s) => (s === "li.ds" ? li : s === "ol.steps" ? list : null) });
    page.sandbox.reply = { said: "Step 1 on 59 done", stamp: "CCC", board: "B2" };
    const ev = { target: { closest: (s) => (s === ".ds-n" ? btn : null) },
      preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {} };
    page.sandbox.document.clicks.forEach((fn) => fn(ev));
    await settle();
    check("a step press marks the step", on.has("done"), "the step was not struck through");
    check("a step press posts the tick", page.sandbox.fetched.some((u) => u.indexOf("/api/tick") === 0),
      "never told the server: " + page.sandbox.fetched.join(", "));
    page.sandbox.reply = { state: "idle", message: "", asks: {}, stamp: "CCC", board: "B2" };
    page.timers.splice(0).forEach((t) => t.fn());
    await settle();
    check("the page's own move does not reload it", page.reloads.count === before,
      `reloaded ${page.reloads.count - before} times, wanted none`);
  }

  // 12. Ticking the last step on a job with no unsent drafts closes the job on the
  //     server (`closed: true`), marks the card Done, and hides its row on the week strip.
  {
    const page = makePage({ stamp: "AAA" });
    await settle();
    const on = new Set();
    const li = element({ classList: {
      add: (c) => on.add(c), remove: (c) => on.delete(c), contains: (c) => on.has(c),
      toggle: (c, f) => { if (f === undefined ? !on.has(c) : f) on.add(c); else on.delete(c); },
    } });
    const list = element({ querySelectorAll: () => [li] });
    const btn = element({ dataset: { stepJob: "98", step: "1" },
      closest: (s) => (s === "li.ds" ? li : s === "ol.steps" ? list : null) });
    const badge = element({ textContent: "2", hidden: false });
    const weekRow = element({ hidden: false });
    const otherRow = element({ hidden: false });
    const weekCol = element({
      querySelector: (s) => (s === ".wd-c" ? badge : null),
      querySelectorAll: (s) => (s === "[data-week-job]" ? [weekRow, otherRow] : []),
    });
    weekRow.closest = (s) => (s === "li.wd" ? weekCol : null);
    const origQSA = page.sandbox.document.querySelectorAll;
    page.sandbox.document.querySelectorAll = (sel) => {
      if (sel === '[data-week-job="98"]') return [weekRow];
      return origQSA(sel);
    };
    page.sandbox.reply = { said: "All steps done. 98 closed", closed: true, stamp: "DDD", board: "B3" };
    const ev = { target: { closest: (s) => (s === ".ds-n" ? btn : null) },
      preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {} };
    page.sandbox.document.clicks.forEach((fn) => fn(ev));
    await settle();
    check("ticking the last step hides the job on the week strip", weekRow.hidden === true,
      "the job stayed visible on the week strip after closing");
    check("ticking the last step updates the week strip count", badge.textContent === "1",
      `badge read "${badge.textContent}", wanted "1"`);
  }

  // A sign-in walk is progress, not a fault: the run buttons wait for it, the
  // Log in button says it is working, and the page keeps watching for its end.
  {
    const page = makePage({ stamp: "AAA" });
    page.sandbox.reply = { state: "signing_in", message: "Approve the Slack connection",
      url: "https://example.test/oauth", asks: {}, stamp: "AAA" };
    await settle();
    const note = page.ids["refresh-note"];
    check("a sign-in reads as progress", note.className.indexOf("run") > -1,
      `note class was "${note.className}"`);
    check("a sign-in keeps its link on screen", page.ids["login-link"].hidden === false,
      "the browser approval link was hidden");
    check("a sign-in keeps watching", page.timers.length > 0,
      "no follow-up poll, so the end of the sign-in would never arrive");
  }

  // 13. A quiet background scout check never disables buttons and never reloads.
  {
    const page = makePage({ stamp: "AAA" });
    page.sandbox.reply = {
      state: "idle", message: "Last refreshed 15:34", asks: {}, stamp: "AAA",
      scout: { state: "running", said: "Checking Asana and Slack…" }, auto_mins: 15,
    };
    await settle();
    check("a running scout does not reload", page.reloads.count === 0,
      `reloaded ${page.reloads.count} times`);
    check("a running scout shows the check chip",
      page.ids["refresh-note"].className.indexOf("run") > -1,
      `note class was "${page.ids["refresh-note"].className}"`);
    page.sandbox.reply = {
      state: "idle", message: "Up to date · checked 17:20", asks: {}, stamp: "AAA",
      scout: { state: "done", said: "Up to date" }, auto_mins: 15,
    };
    page.timers.splice(0).forEach((t) => t.fn());
    await settle();
    check("a quiet scout finish never reloads", page.reloads.count === 0,
      `reloaded ${page.reloads.count} times`);
    check("a quiet scout updates the header timestamp",
      page.ids["refresh-note"].textContent === "Up to date · checked 17:20",
      `header read "${page.ids["refresh-note"].textContent}"`);
  }

  // 14. Copying a draft and returning to the window triggers POST /api/scout,
  //     unless the owner already pressed Posted it on the page.
  {
    let fakeNow = 1000000;
    const page = makePage({ stamp: "AAA" });
    page.sandbox.Date = { now: () => fakeNow };
    await settle();
    const copyBtn = element({ closest: (s) => (s === ".act-draft" ? element() : null) });
    const copyEv = {
      target: {
        closest: (s) => (s.indexOf(".copy") > -1 ? copyBtn : null),
      },
      preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {},
    };
    page.sandbox.document.clicks.forEach((fn) => fn(copyEv));
    fakeNow += 25000;
    page.sandbox.window.focusListeners.forEach((fn) => fn());
    page.timers.splice(0).forEach((t) => t.fn());
    await settle();
    check("returning after copying a draft triggers /api/scout",
      page.sandbox.fetched.some((u) => u.indexOf("/api/scout") === 0),
      "fetched: " + page.sandbox.fetched.join(", "));
  }

  // 15. A background delta refresh finishing while the owner is typing never reloads.
  {
    const page = makePage({ stamp: "AAA", typed: "let me rewrite this first" });
    page.sandbox.reply = { state: "done", message: "Updated", asks: {}, stamp: "EEE", board: "B4" };
    await settle();
    check("a background delta refresh never reloads over typing", page.reloads.count === 0,
      `reloaded ${page.reloads.count} times`);
    check("a background delta refresh offers the reload button", page.ids.changed.hidden === false,
      "changed button stayed hidden");
  }

  // 16. Opening the Sync & Activity popover holds auto-reload back until closed,
  //     and pressing Check now triggers /api/scout?force=1.
  {
    const page = makePage({ stamp: "AAA", syncOpen: true });
    page.sandbox.reply = { state: "idle", message: "Updated", asks: {}, stamp: "FFF", board: "B5" };
    await settle();
    check("an open sync popover is not reloaded away", page.reloads.count === 0,
      `reloaded ${page.reloads.count} times while reading recent updates`);
    page.ids["scout-now"].listeners.click();
    await settle();
    check("Check now posts /api/scout?force=1",
      page.sandbox.fetched.some((u) => u.indexOf("/api/scout?force=1") === 0),
      "fetched: " + page.sandbox.fetched.join(", "));
  }

  const bad = results.filter((r) => !r.pass);
  for (const r of results) {
    console.log(`  ${r.pass ? "ok  " : "FAIL"}  ${r.name}${r.pass ? "" : "\n          " + r.detail}`);
  }
  if (bad.length) {
    console.log(`\n${bad.length} of ${results.length} failed.`);
    process.exit(1);
  }
  console.log(`\nAll ${results.length} hold.`);
}

run().catch((e) => { console.error(e); process.exit(1); });
