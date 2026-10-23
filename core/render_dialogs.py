#!/usr/bin/env python3
"""The two dialogs: how to work this thing, and what to set.

Split out of `render_desk.py`, which had grown past 2200 lines. These are 240
lines of almost entirely static markup that nothing else on the page depends
on, and having them inline meant scrolling through the whole manual to reach
the code that draws a ticket.

`THEMES` lives here because Settings is the only thing that reads it, and
`check.py` holds it against the stylesheet: a theme offered here and not styled
in `base.css`, or styled and never offered, is a failed check rather than a
dead entry in a menu.
"""

from __future__ import annotations

import settings
from render import esc


def help_dialog() -> str:
    """How to work the thing, one keystroke away from every view.

    Written to be read standing up, thirty seconds before a standup. Every
    section answers "what do I press" first; the reason it works that way comes
    after, in one line, or not at all. The long version of any of this is in
    README.md, which is where it belongs.
    """
    cmd = settings.command()
    short = esc(settings.client_short())
    ticks = [
        (cmd, "Where everything is. Free, instant."),
        (f"{cmd} 4", "Job 4 is finished, nothing comes back."),
        (f"{cmd} 2 -w Alex", "Sent. Now sitting with Alex, not you."),
        (f"{cmd} 2 --mine", "They replied. Yours again."),
        (f"{cmd} 5 --dropped", "It went away. Add <code>-n</code> and why."),
        (f"{cmd} 1 --undo", "Forget that state entirely."),
    ]
    said = "".join(
        f'<li><span class="said">{esc(a)}</span><span class="does">{b}</span></li>'
        for a, b in ticks
    )
    asks = [
        ("what do you mean by that term?", "Answers on the card and stays there."),
        ("rewrite this with the latest from the refinement thread",
         "Reads the thread, rewrites the draft, says what changed."),
        ("is that true about refinement?",
         "Checks it. Says so when it cannot find the source."),
        ("this is done, close it", f"Moves the job, same as <code>{esc(cmd)} 29</code>."),
        ("I have sent this, it is with Alex now", "Parks it with them."),
    ]
    script_only = (
        "Japanese only" if settings.language().get("client") == "ja" else "the script only"
    )
    ask_rows = "".join(
        f'<li><span class="said">{esc(a)}</span><span class="does">{b}</span></li>'
        for a, b in asks
    )
    return f"""
<dialog class="help" id="help">
  <div class="help-in">
    <button class="help-close" type="button">Close</button>
    <h2>How to use this</h2>
    <p class="lead">Two tabs off one file. <b>{short} my work</b> is what you do.
    <b>{short} what I say</b> is the words for the next meeting.</p>

    <h3>The three buttons</h3>
    <ul class="say-list">
      <li><span class="said">Refresh</span><span class="does">Re-reads Asana and
      every thread behind your open work, then rewrites this tab: what moved,
      what closed, which drafts the threads have overtaken. Two to three
      minutes.</span></li>
      <li><span class="said">Update prep</span><span class="does">On <b>what I
      say</b>. Same sweep, then writes the script on top. Refresh plus the
      words, never less.</span></li>
      <li><span class="said">Ask or change</span><span class="does">On every job,
      ticket and draft. Asks a question or makes a change to that one
      thing.</span></li>
    </ul>
    <p>Refresh leaves the script alone on purpose, so a sweep at 17:00 cannot
    throw away the wording you fixed at 16:00. When the script is older than the
    board, both tabs say so in amber.</p>

    <h3>Ask or change</h3>
    <p>Type, press <kbd>enter</kbd>, get an answer on the card in a minute or
    two. Questions and instructions both go in the same box:</p>
    <ul class="say-list">{ask_rows}</ul>
    <p>You never say which job you mean. The card sends its own draft, threads
    and earlier answers along with the question.</p>
    <p><b>Two things it will not do:</b> send anything to anybody, and decide by
    itself that a job is finished. It tells you it looks done and leaves it.</p>
    <p>Ask several at once, they queue. <b>Cancel</b> stops one.
    <b>Ask about this answer</b> digs into an answer, carrying the exchange with
    it, so <span class="said">why?</span> is a whole question there. <b>&times;</b>
    forgets one for good.</p>
    <p>The box above the tickets is for the day rather than one job: what to
    start on, whether tomorrow is covered. It answers in job numbers.</p>
    <p><b>For a long conversation</b>, press <b>Ask</b> in the header or
    <kbd>a</kbd>. The side panel is the same record as the cards, drawn as a
    chat: everything open, newest at the bottom, and each question follows on
    from the last answer until you press <b>New topic</b>. Pick which job it is
    about at the top; the strip under it names the job, its ticket and where it
    stands, with a link to its card. <b>Open in side panel</b> on any card's box
    carries what you typed over. <b>&middot;&middot;&middot;</b> clears this
    conversation or every one, and <b>&times;</b> on a question forgets just
    that one.</p>
    <p>Every answer says which model wrote it and how long it took. Pick the
    model next to the box: Opus writes the best client-language text, Sonnet is
    a third of the wait, Haiku is for a quick lookup. The choice sticks.</p>
    <p>Same thing from a terminal: <code>{esc(cmd)} ask 8 "..."</code>, and the answer
    lands on the same card.</p>

    <h3>Moving a job</h3>
    <p>After posting a draft, press <b>Posted it</b> under its words. It says
    what it will do before you press it: wait on whoever replies, close the
    job, or note that one more draft is still to send. The arrow beside it is
    for when that is wrong, someone else answering or nothing coming back.
    Copying a draft lights its button up so it is waiting when you come back.</p>
    <p>For anything else, press its status, the coloured label on the right of every job. It lists
    the moves open from where the job stands, and each one is the same as
    <code>./tick.py</code> in a terminal, with an Undo for six seconds.</p>
    <ul class="say-list">
      <li><span class="said">With you</span><span class="does">Sent, waiting
      on who (filled from the draft, so <kbd>x</kbd> then Enter), done,
      dropped. The wait gets a chase day two working days out, and the next
      refresh looks for your message and their answer.</span></li>
      <li><span class="said">Waiting on Alex</span><span class="does">They
      replied, done, now with someone else, dropped.</span></li>
      <li><span class="said">Not yet</span><span class="does">Start it now, or
      close it.</span></li>
      <li><span class="said">Done</span><span class="does">Open it again.</span></li>
    </ul>
    <p><b>Sending a message is not finishing.</b> That is why it is a status
    and not a checkbox: if a reply is coming, the job goes to the person, not
    to done.</p>
    <p><b>Who it waits on is a person, not a sentence.</b> The picker starts
    with whoever the draft is addressed to, then the people on that ticket, the
    ones you wait on most, and the teams. Type to search by name, surname,
    kanji or team. <kbd>Enter</kbd> picks, <b>+</b> or <kbd>Shift</kbd>+<kbd>Enter</kbd>
    adds another, <kbd>Backspace</kbd> takes the last one off, and
    <span class="said">Use &ldquo;&hellip;&rdquo;</span> takes someone the board has not
    met yet. Their initials show on the job from then on.</p>
    <p>The same from a terminal:</p>
    <ul class="say-list">{said}</ul>

    <h3>The week, and your meetings</h3>
    <p>The strip draws every meeting on your Google Calendar beside your jobs.
    Work meetings are drawn in full, rooms with {short} in them in red; the rest fold
    under <b>N other meetings</b>. <b>Join</b> opens the Meet, <b>Notes</b> the
    notes doc, the title the event itself. From ten minutes before a meeting
    the header offers <b>Join &hellip; in 5m</b>.</p>
    <p>The calendar is read hourly through the working day and again just
    after a work meeting ends, which is when Meet attaches Gemini's notes.
    <b>Calendar 12m ago</b> reads it now. When notes arrive that the board has
    not read, <b>2 meeting notes to fold in</b> appears: it runs a refresh, which
    reads them and puts what was said on the tickets it touches.</p>
    <p>For that to happen on its own, turn on <b>Take notes with Gemini</b> in
    the Meet. Nothing here ever writes to the calendar.</p>

    <h3>The numbers</h3>
    <p>A job keeps its number until it closes, which is why &ldquo;do 3&rdquo;
    needs nothing else said, and why the list runs 3, 5, 2, 7 with gaps. The
    numbers on <b>what I say</b> are different: those are {short}'s own running order.</p>

    <h3>Cards, and what is folded</h3>
    <p>Open is what needs you. Everything else is folded, and the fold says what
    is behind it, so <b>Timeline, 10 moves today</b> has already answered you.
    The page remembers what you opened, per card.</p>

    <h3>A longer conversation</h3>
    <p><code>{esc(cmd)} chat</code> opens a Claude Code chat on this folder: for work
    across three jobs, something you want to argue about, anything where you are
    reading code together. Same board, same rules, so &ldquo;do 3&rdquo; works
    with nothing else said. <b>Copy for a chat</b> on any ask box hands the job
    and your words over, and it is what <kbd>enter</kbd> does on a page opened
    from disk with no server behind it.</p>

    <h3>If a button says it cannot read anything</h3>
    <p>The sweep needs Asana and Slack. <code>{esc(cmd)} mcp</code> says where those
    connections stand, and <code>./setup-mcp.sh</code> fixes them: it adds
    anything missing, then opens a browser tab per approval. <b>Log in</b> on the
    page does the same for a grant that has merely lapsed.</p>

    <h3>Where this page lives</h3>
    <p>This laptop only. A small server on <code>127.0.0.1</code>, which nothing
    else can reach, and the URL carries a random key as well. The private repo
    holds the code and the prompts, never <code>state/</code> or
    <code>output/</code>, so your tickets and drafts have never left the
    machine.</p>

    <h3>Keys</h3>
    <p><kbd>1</kbd> your work, <kbd>2</kbd> what you say, <kbd>/</kbd> find
    anything, <kbd>s</kbd> {script_only}, <kbd>,</kbd> settings,
    <kbd>a</kbd> the Ask panel, <kbd>[</kbd> <kbd>]</kbd> the week before
    and after, <kbd>?</kbd> this.</p>
    <p>On the To do list: <kbd>j</kbd> <kbd>k</kbd> move down and up,
    <kbd>x</kbd> opens the status of the job you are on, <kbd>&uarr;</kbd>
    <kbd>&darr;</kbd> walk its moves, <kbd>enter</kbd> opens its card,
    <kbd>u</kbd> undoes the last move.</p>
  </div>
</dialog>"""


# Every theme is a wash behind the cards and nothing else: no theme touches a
# semantic colour, a text colour or a size, so red still means the owner's work and the
# type is exactly as legible on all of them.
THEMES = (
    ("plain", "Plain", "White cards on grey. What this page always looked like."),
    ("kraken", "Kraken", "Aubergine, cream and Kraken pink, with the logo in the bar."),
    ("ukiyoe", "Ukiyo-e", "Washi paper under Hokusai's Fuji from Shichirigahama."),
    ("ai", "Ai 藍", "Indigo, deepest at the top, like a dipped cloth."),
    ("sakura", "Sakura 桜", "Warm pink, with a cherry branch in blossom."),
    ("koke", "Koke 苔", "Quiet moss green, for an afternoon on one ticket."),
    ("sumi", "Sumi 墨", "Near monochrome, for when colour is in the way."),
    ("yoru", "Yoru 夜", "Dark. For the evening, when a white page is too much."),
)


def settings_dialog() -> str:
    """Personalisation, on Slack's terms: the background, and nothing else.

    A theme here is deliberately powerless. It sets one wash behind the cards
    and cannot reach a semantic colour or a font size, because the page is read
    sixty minutes before the owner speaks to the client, and a theme that made a
    client term a shade harder to read would be a bug with a settings entry.
    """
    swatches = "".join(
        f"""
      <button type="button" class="thm" data-theme-set="{esc(key)}"
              aria-pressed="false">
        <span class="thm-sw thm-{esc(key)}"></span>
        <span class="thm-t"><b>{esc(name)}</b>{esc(why)}</span>
        <span class="thm-on">Using this</span>
      </button>"""
        for key, name, why in THEMES
    )
    owner = settings.owner_name()
    ver = esc(settings.version_label())
    if owner:
        ws = settings.workstream()
        who = (
            f"{esc(settings.desk_name())} {ver} for <b>{esc(owner)}</b>"
            + (f", on {esc(ws)}" if ws else "")
            + f" with {esc(settings.client_name())}."
        )
    else:
        who = f"Nobody has set this {esc(settings.desk_name())} {ver} up yet."
    who += (
        " The owner, the client, the rooms and the terminal command live in"
        " <code>config.json</code>, and the house style in <code>me/profile.md</code>."
        ' <a href="/setup">Open the setup page</a> to change any of them, or run'
        " <code>./setup.py</code> in the folder."
    )
    return f"""
<dialog class="help set" id="settings">
  <div class="help-in">
    <button class="help-close" type="button">Close</button>
    <h2>Settings</h2>
    <p class="lead">Kept in this browser, on this machine. Nothing here changes
    the board.</p>

    <h3>Whose DANDORI this is</h3>
    <p>{who}</p>

    <h3>Theme</h3>
    <p>The background only. Every colour that means something (red is
    yours, amber is blocked, blue is with someone else, green is closed)
    and every text size stay exactly as they are.</p>
    <div class="thms">{swatches}</div>

    <h3>Motion</h3>
    <p>Presses, folds and the wave when a job closes. Turn it off here, or
    system-wide with Reduce Motion, which this page already follows.</p>
    <div class="thms">
      <button type="button" class="thm wide" data-motion-set="on"
              aria-pressed="false">
        <span class="thm-t"><b>On</b>Everything under a quarter of a
        second.</span>
        <span class="thm-on">Using this</span>
      </button>
      <button type="button" class="thm wide" data-motion-set="off"
              aria-pressed="false">
        <span class="thm-t"><b>Off</b>No transitions, no wave.</span>
        <span class="thm-on">Using this</span>
      </button>
    </div>

    <h3>Opening view</h3>
    <p>Which half this page starts on. By default it decides by the clock:
    the script before the session, your work after it.</p>
    <div class="thms">
      <button type="button" class="thm wide" data-open-set="auto"
              aria-pressed="false">
        <span class="thm-t"><b>By the clock</b>The script until the session
        starts, then your work.</span>
        <span class="thm-on">Using this</span>
      </button>
      <button type="button" class="thm wide" data-open-set="desk"
              aria-pressed="false">
        <span class="thm-t"><b>Always my work</b>Open on the tickets.</span>
        <span class="thm-on">Using this</span>
      </button>
      <button type="button" class="thm wide" data-open-set="standup"
              aria-pressed="false">
        <span class="thm-t"><b>Always what I say</b>Open on the script.</span>
        <span class="thm-on">Using this</span>
      </button>
    </div>

    <h3>Connections</h3>
    <p>A sweep needs a model credential and it needs Asana and Slack. These are
    three separate things: every server can report connected while a prep dies
    six minutes in, because the credential has lapsed and nothing checked it.
    This checks all three, the way a sweep would.</p>
    <div class="chk-bar">
      <button type="button" class="btn chk-go" data-check>Test connections</button>
      <span class="chk-when"></span>
    </div>
    <ul class="chk-rows"></ul>
    <p class="chk-note"></p>
    <p>Only you can approve a grant in a browser, so a lapsed one needs
    <b>Log in</b> in the header, or <code>./setup-mcp.sh</code> in a terminal.
    A credential that will not answer is usually a locked 1Password:
    <code>op signin</code>.</p>

    <h3>This page</h3>
    <p>Reloads itself when the board moves, and holds the reload back only when
    you are half way through typing in a composer. Folds, the open tab and your
    place on the page are all remembered in this browser.</p>
    <div class="chk-bar">
      <button type="button" class="btn" data-forget-ui>Forget what I opened</button>
      <span class="chk-when" data-forget-said></span>
    </div>
  </div>
</dialog>"""
