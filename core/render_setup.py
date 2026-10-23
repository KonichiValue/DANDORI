#!/usr/bin/env python3
"""The setup page: every step a new DANDORI needs, each one a button.

    python3 render_setup.py [out.html]   draw it once, for a look without a server

serve.py draws it on every load of `/setup`, and `/` sends a desk that has no
answers or no board here. The form is filled from what `config.json` already
says, so opening it again later is how an owner changes an answer. Each step's
state comes from `/api/setup/state`; `static/setup.js` redraws them as they
move.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import settings
import setup
import setup_page
from render import asset, esc

LANGUAGES = (("en", "English"), ("ja", "Japanese"), ("de", "German"), ("fr", "French"),
             ("es", "Spanish"), ("it", "Italian"), ("nl", "Dutch"), ("pt", "Portuguese"),
             ("pl", "Polish"), ("ko", "Korean"), ("zh", "Chinese"))
KINDS = (("weekly", "Weekly review with the client"), ("rollup", "Programme-wide daily or rollup"),
         ("huddle", "Kraken-side team huddle"), ("other", "Something else"))


def timezones() -> list[str]:
    try:
        from zoneinfo import available_timezones

        zones = available_timezones()
    except Exception:  # noqa: BLE001  (no tz database: offer the common ones)
        zones = set()
    keep = sorted(z for z in zones if "/" in z and not z.startswith(("Etc/", "SystemV/", "posix/", "right/")))
    return keep or ["Asia/Tokyo", "Europe/London", "America/New_York", "Australia/Sydney"]


def machine_tz() -> str:
    return setup.machine_tz()


def val(cfg: dict, path: str, fallback="") -> str:
    got = setup_page.get(cfg, path)
    if isinstance(got, list):
        return ", ".join(str(x) for x in got)
    return str(got if got not in (None, "") else fallback)


def field(cfg: dict, path: str, label: str, hint: str = "", *, kind: str = "text",
          fallback="", place: str = "", need: bool = False, wide: bool = False) -> str:
    value = val(cfg, path, fallback)
    extra = ' inputmode="numeric"' if kind == "number" else ""
    kind = "text" if kind == "number" else kind
    return f"""
<label class="f{' wide' if wide else ''}" data-for="{esc(path)}">
  <span class="f-l">{esc(label)}{'' if need else ' <i>optional</i>'}</span>
  <input type="{kind}" name="{esc(path)}" value="{esc(value)}" placeholder="{esc(place)}"{extra}
         autocomplete="off" spellcheck="false">
  {f'<span class="f-h">{hint}</span>' if hint else ''}
  <span class="f-e"></span>
</label>"""


def select(cfg: dict, path: str, label: str, options, hint: str = "", fallback: str = "",
           wide: bool = False) -> str:
    current = val(cfg, path, fallback)
    opts = list(options)
    if current and current not in [o for o, _ in opts]:
        opts.insert(0, (current, current))
    body = "".join(
        f'<option value="{esc(o)}"{" selected" if o == current else ""}>{esc(t)}</option>'
        for o, t in opts
    )
    return f"""
<label class="f{' wide' if wide else ''}" data-for="{esc(path)}">
  <span class="f-l">{esc(label)}</span>
  <select name="{esc(path)}">{body}</select>
  {f'<span class="f-h">{hint}</span>' if hint else ''}
  <span class="f-e"></span>
</label>"""


def day_boxes(name: str, chosen) -> str:
    chosen = set(chosen or [])
    return '<span class="days">' + "".join(
        f'<label><input type="checkbox" name="{esc(name)}" value="{d}"'
        f'{" checked" if d in chosen else ""}><span>{d}</span></label>'
        for d in setup.DAYS
    ) + "</span>"


def project_row(p: dict) -> str:
    url = p.get("gid") and f"https://app.asana.com/0/{p['gid']}" or ""
    return f"""
<div class="row" data-row="project">
  <input type="text" data-k="url" value="{esc(url)}" placeholder="Paste the project's Asana link" spellcheck="false">
  <input type="text" data-k="name" value="{esc(p.get('name', ''))}" placeholder="What it is called">
  <button type="button" class="x" data-drop title="Remove">&times;</button>
</div>"""


def room_row(r: dict) -> str:
    kind = r.get("kind") or ("other" if r.get("name") else "weekly")
    opts = "".join(f'<option value="{k}"{" selected" if k == kind else ""}>{esc(t)}</option>'
                   for k, t in KINDS)
    return f"""
<div class="row room" data-row="room">
  <input type="text" data-k="name" value="{esc(r.get('name', ''))}" placeholder="Name, as in your calendar">
  <input type="time" data-k="time_local" value="{esc(r.get('time_local', ''))}">
  {day_boxes('days', r.get('days') or [])}
  <select data-k="kind">{opts}</select>
  <button type="button" class="x" data-drop title="Remove">&times;</button>
</div>"""


def form(cfg: dict) -> str:
    tz_now = cfg.get("timezone") or machine_tz()
    region_guess = {"Asia": "apac", "Australia": "apac", "Pacific": "apac",
                    "America": "us"}.get(tz_now.split("/")[0], "eu")
    lang = val(cfg, "client.language", "en")
    m = cfg.get("meeting") or {}
    db_on = bool(val(cfg, "database.mcp"))
    rnd = cfg.get("prioritization") or {}
    projects = [p for p in (cfg.get("asana") or {}).get("projects") or [] if p.get("gid")]
    rooms = m.get("also") or []
    adv_open = " open" if (db_on or rnd or rooms) else ""
    return f"""
<form id="answers" autocomplete="off">
<fieldset><legend>1. You</legend>
  {field(cfg, "owner.name", "Your full name", "As Asana and Slack show it. Pre-filled from your Mac if available.", need=True, place="Alex Example")}
  {field(cfg, "owner.email", "Your Kraken email", "", kind="email", need=True, place="alex.example@kraken.tech")}
  {field(cfg, "owner.workstream", "Your workstream", "What your tickets are about: billing, payments, onboarding, migration.", place="billing")}
  {field(cfg, "owner.role", "Your role", "", fallback="Client Delivery Lead")}
</fieldset>

<fieldset><legend>2. The client</legend>
  {field(cfg, "client.name", "Client name", "Typing this auto-fills the short name and terminal command.", need=True, place="Example Energy")}
  {field(cfg, "client.short", "Short name", "Two to four letters, for the page tabs: <i>EE my work</i>.", need=True, place="EE")}
  {select(cfg, "client.language", "You write to them in", LANGUAGES, "Drafts to the client are in this language. Drafts to Kraken are always English.", "en")}
  {field(cfg, "client.domains", "Their email domains", "Comma separated. Tells a client meeting from a Kraken one.", place="example-energy.com")}
  <div class="lang-more"{'' if lang != 'en' else ' hidden'}>
    {field(cfg, "language.level", "Your level in it", "Scripts you read aloud are pitched for it.", place="N2 spoken")}
    {field(cfg, "language.register", "The register you write in", "", place="ですます, plain not stiff keigo")}
    <label class="f tick"><input type="checkbox" name="language.readings"{' checked' if (cfg.get('language') or {}).get('readings') else ''}>
      <span>Add readings over hard words, <code>{{漢字|かんじ}}</code> style</span></label>
  </div>
</fieldset>

<fieldset><legend>3. Your standup with the client</legend>
  {field(cfg, "meeting.name", "What it is called in your calendar", "DANDORI writes your script for it and folds its notes in as it ends.", need=True, place="EE x Kraken Billing Standup", wide=True)}
  {field(cfg, "meeting.time_local", "Starts at", "", kind="time", fallback="10:30", need=True)}
  {field(cfg, "meeting.minutes", "Minutes long", "", kind="number", fallback="30", need=True)}
  <div class="f wide" data-for="meeting.days"><span class="f-l">On</span>
    {day_boxes("meeting.days", m.get("days") or ["Mon", "Wed"])}<span class="f-e"></span></div>
</fieldset>

<fieldset><legend>4. Asana &amp; Slack</legend>
  <p class="lead">Paste the Asana project links your tickets live in (or leave empty and the first refresh searches everything assigned to you).</p>
  <div class="rows" data-rows="project">{''.join(project_row(p) for p in projects) or project_row({})}</div>
  <button type="button" class="btn add" data-add="project">Add a project</button>
  <span class="f-e" data-for="asana.projects"></span>
  {field(cfg, "slack.channel_hints", "Slack channels your tickets are discussed in", "Comma separated, e.g. #ext-proj-example-billing.", place="#ext-proj-example-billing, #team-example-billing", wide=True)}
</fieldset>

<details class="adv"{adv_open}>
  <summary>Optional &amp; advanced settings <span>timezone, extra meetings, database replica, prioritisation round, app port</span></summary>

  <fieldset><legend>Where and when (auto-detected)</legend>
    {select(cfg, "timezone", "Your working timezone", [(z, z.replace("_", " ")) for z in timezones()], f"This Mac says {esc(machine_tz())}.", tz_now)}
    {select(cfg, "models.region", "Model region", [(k, f"{v} ({k})") for k, v in setup.REGIONS.items()], "Where your LiteLLM models run. <a href='https://hub.ai.ktl.net/models' target='_blank'>hub.ai.ktl.net/models</a> lists them.", region_guess)}
  </fieldset>

  <fieldset><legend>Account IDs &amp; links</legend>
    {field(cfg, "owner.slack_user_id", "Your Slack member id", "In Slack: your photo, <b>Profile</b>, <b>&#8942;</b>, <b>Copy member ID</b> (starts with U).", place="U01ABCDEF")}
    {field(cfg, "owner.asana_user_gid", "Your Asana user id", "Leave blank and the first refresh looks it up.", kind="number")}
    {field(cfg, "asana.workspace_gid", "Asana workspace id", "Leave blank and the first refresh finds it.", kind="number")}
    {field(cfg, "client.support_url", "Support site account page", "With <code>&lt;account&gt;</code> where the account number goes.", place="https://support.example-kraken.energy/accounts/<account>", wide=True)}
  </fieldset>

  <fieldset><legend>Other meetings &amp; notes</legend>
    {field(cfg, "meeting.notes_title", "Main standup notes title", "The Notion or Gemini notes title, if different from the meeting name.", wide=True)}
    <p class="lead">Other recurring meetings you speak in (a weekly review, a programme daily, a team huddle).</p>
    <div class="rows" data-rows="room">{''.join(room_row(r) for r in rooms)}</div>
    <button type="button" class="btn add" data-add="room">Add a meeting</button>
    <span class="f-e" data-for="meeting.also"></span>
  </fieldset>

  <fieldset class="opt"><legend>
    <label class="tick"><input type="checkbox" name="database.on" data-opens="db"{' checked' if db_on else ''}>
    <span>Production data replica <i>optional</i></span></label></legend>
    <p class="lead">A read-only replica MCP lets DANDORI count accounts or check a charge.</p>
    <div class="more" data-more="db"{'' if db_on else ' hidden'}>
      {field(cfg, "database.mcp", "MCP server name", "", need=True, place="ktdb-example")}
      {field(cfg, "database.command", "Command that serves it", "", fallback="~/.local/bin/ktdb-mcp")}
      {field(cfg, "database.conn", "Connection name (KTDB_CONN)", "")}
    </div>
  </fieldset>

  <fieldset class="opt"><legend>
    <label class="tick"><input type="checkbox" name="prioritization.on" data-opens="round"{' checked' if rnd else ''}>
    <span>A build prioritisation round <i>optional</i></span></label></legend>
    <p class="lead">A lead who posts the build order every couple of weeks. DANDORI marks the
    round on your week and keeps a note to them ready.</p>
    <div class="more" data-more="round"{'' if rnd else ' hidden'}>
      {field(cfg, "prioritization.who", "Who runs it", "", need=True)}
      {field(cfg, "prioritization.slack_user_id", "Their Slack member id", "")}
      {field(cfg, "prioritization.channel", "Channel it is posted in", "", place="#team-example-cdl")}
      {field(cfg, "prioritization.channel_id", "That channel's id", "", place="C0123ABCD")}
      {field(cfg, "prioritization.anchor", "The date of one round", "", kind="date", need=True)}
      {field(cfg, "prioritization.every_days", "Every how many days", "", kind="number", fallback="14", need=True)}
      {field(cfg, "prioritization.thread_at", "Posted at", "", kind="time", fallback="08:30")}
    </div>
  </fieldset>

  <fieldset><legend>The app</legend>
    {field(cfg, "desk.name", "App name", "In the Dock and the window title.", fallback="DANDORI", need=True)}
    {field(cfg, "desk.command", "Terminal command", "<code>DANDORI</code> always works too.", fallback="DANDORI", need=True)}
    {field(cfg, "desk.port", "Port", "Change it only if 8787 is taken.", kind="number", fallback="8787", need=True)}
    {field(cfg, "desk.morning_sweep", "Morning refresh at", "Only if you turn it on with <code>DANDORI morning</code>.", kind="time", fallback="08:15")}
  </fieldset>
</details>

<div class="save-bar">
  <button type="submit" class="btn primary">Save my answers</button>
  <span class="save-said" aria-live="polite"></span>
</div>
</form>
<template id="tpl-project">{project_row({})}</template>
<template id="tpl-room">{room_row({})}</template>"""


STEPS = (
    ("claude", "Install Claude Code",
     "The AI that does the reading and writing. DANDORI runs it in the background, on your Mac.",
     '<button type="button" class="btn primary" data-run="install_claude">Install Claude Code</button>'),
    ("gateway", "Connect it to Kraken's model gateway",
     """<ol class="how">
<li>Install the <b>1Password</b> app and sign in. In <b>1Password, Settings, Developer</b>,
turn on <b>Integrate with 1Password CLI</b>.</li>
<li>With Zscaler on, open <a href="https://litellm.kraken.tech" target="_blank">litellm.kraken.tech</a>,
sign in and create an API key.</li>
<li>In your <b>Employee</b> vault, save it as an item called exactly
<code>LiteLLM API Key</code>, with the key in its <b>credential</b> field.</li>
<li>Press the button. A Terminal window installs the Kraken AI harness and points
Claude Code at the gateway. Answer what it asks, allow 1Password if it asks.</li>
</ol>
<p class="aside">Stuck? <b>#litellm</b> is the channel for keys and the gateway.</p>""",
     '<button type="button" class="btn primary" data-run="gateway">Open the gateway setup</button>'),
    ("connect", "Connect Asana, Slack, Google and Notion",
     """<ol class="how">
<li>Open <a href="https://hub.ai.ktl.net" target="_blank">hub.ai.ktl.net</a>,
<b>MCP gateway, My servers</b>, and turn on Asana, Slack, Google and Notion.</li>
<li>Press the button. It adds them to Claude Code and opens a browser tab for each
one that needs approving.</li>
</ol>
<p class="aside">Missing one in the hub? Ask in <b>#team-ai-foundations-requests</b>.</p>""",
     '<button type="button" class="btn primary" data-run="connect">Connect them</button>'),
    ("answers", "Answer the setup questions",
     "Who you are, your client, your standup, where your tickets live. Two minutes. "
     "Saved on this Mac only, never uploaded.",
     '<button type="button" class="btn primary" data-open-form>Answer them</button>'),
    ("profile", "Teach it your house style",
     "The one that makes your drafts sound like you and not like a robot. "
     "Claude interviews you for ten minutes: how you write to the client, who your people "
     "are, what must never reach them. It looks up your ids and meetings itself. "
     "You can also edit <code>me/profile.md</code> by hand.",
     '<button type="button" class="btn" data-run="interview">Start the interview</button>'),
    ("app", "Install the app",
     "Puts DANDORI in your Applications folder (drag it to the Dock), adds the "
     "<code>DANDORI</code> command, and keeps the page running from login.",
     '<button type="button" class="btn primary" data-run="install_app">Install the app</button>'),
    ("refresh", "Build your board",
     "The first refresh reads everything assigned to you in Asana and its Slack threads. "
     "Five to ten minutes, so go get a coffee ☕ After that a refresh takes a few.",
     '<button type="button" class="btn primary" data-refresh>Run the first refresh</button>'
     '<a class="btn primary" href="/" data-desk hidden>Open DANDORI</a>'),
)


def render(cfg: dict | None = None, states: list[dict] | None = None) -> str:
    cfg = setup.load() if cfg is None else cfg
    states = {s["id"]: s for s in (states or [])}
    name = settings.desk_name()
    steps = []
    for n, (ident, title, how, buttons) in enumerate(STEPS, 1):
        st = states.get(ident, {"state": "checking", "said": ""})
        body = form(cfg) if ident == "answers" else ""
        steps.append(f"""
<li class="step" id="step-{ident}" data-step="{ident}" data-state="{esc(st['state'])}">
  <div class="st-n"><span>{n}</span></div>
  <div class="st-b">
    <div class="st-top">
      <h2>{esc(title)}</h2>
      <span class="st-said">{esc(st.get('said', ''))}</span>
    </div>
    <div class="st-how">{how if how.startswith('<') else f'<p>{how}</p>'}</div>
    {'<div class="chips"></div>' if ident == 'connect' else ''}
    <div class="st-go">{buttons}</div>
    <div class="st-run" hidden><p class="run-said"></p><a class="run-url btn" target="_blank" hidden>Open the approval page</a><pre class="run-log"></pre></div>
    {f'<div class="form-wrap" hidden>{body}</div>' if body else ''}
  </div>
</li>""")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/png" href="/icon-192.png">
<meta name="theme-color" content="#0d1524">
<title>Set up {esc(name)}</title>
<style>{asset("base.css")}{asset("setup.css")}</style></head>
<body data-view="setup">
<header class="su-head">
  <div class="su-in">
    <p class="su-kicker">Welcome</p>
    <h1>Let's set up {esc(name)} 👋</h1>
    <p class="su-lead">Seven steps, most of them one button. Do them in order and watch
    them turn green. And relax: nothing here sends anything to anyone.</p>
    <div class="su-bar"><span class="su-fill"></span></div>
    <p class="su-count"><span class="su-done">0</span> of {len(STEPS)} done
      <button type="button" class="btn ghost" data-run="recheck">Check again</button>
      <a class="btn ghost" href="/" data-desk hidden>Back to {esc(name)} →</a>
      <span class="su-at"></span></p>
  </div>
</header>
<main class="su-main">
<ol class="steps">{''.join(steps)}</ol>
<p class="su-foot">{esc(name)} {esc(settings.version_label())}. Every step can also be done in Terminal: <code>README.md</code> in
the DANDORI folder has each one as a command, under <i>The whole setup by hand</i>. Your answers
live in <code>clients/&lt;client&gt;/config.json</code> and <code>clients/&lt;client&gt;/profile.md</code>, which git never sees.
Come back here any time from <b>Settings</b> in {esc(name)} to change them.</p>
</main>
<script>window.SETUP={{key:'__DESK_KEY__',boot:'__DESK_BOOT__'}};</script>
<script>{asset("setup.js")}</script>
</body></html>"""


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else str(settings.OUTPUT_DIR / "setup.html"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(), encoding="utf-8")
    print(out)
