#!/usr/bin/env python3
"""Fast, capable in-app AI answers and surgical revisions across DANDORI.

Supports four scopes (`kind`):
- `draft`: explain/fact-check any line in a draft, or revise the draft in place (+ Undo)
- `script`: explain/fact-check speaking lines on `What I say`, or revise them in place (+ Undo)
- `ticket`: ask any question about a ticket/walkthrough (with visual step diagrams & evidence
            thumbnails), or rewrite `prep.explainer` in place (+ Undo)
- `job`: walk through a job's steps concretely, or tighten `where_it_stands` / `steps` (+ Undo)

Defaults to Opus 5.5 with full ticket + prepared findings + evidence screenshots in context
so subtle domain questions ("is mentioning 値引きの金額 right here?", "what do I actually click
for PROD and 稼働確認?") get accurate, visual, plain-English answers in seconds.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

CORE = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE))

import agent  # noqa: E402
import board as B  # noqa: E402
import keep  # noqa: E402
import plugins  # noqa: E402
import render  # noqa: E402
import settings  # noqa: E402
import tick as T  # noqa: E402

_undo_lock = threading.Lock()
_undos: dict[str, dict] = {}
MAX_UNDOS = 60

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.I)
DATA_IMG_RE = re.compile(r"^data:(image/(?:png|jpeg|jpg|webp|gif));base64,([A-Za-z0-9+/=\s]+)$")
IMG_EXT_RE = re.compile(r"\.(png|jpe?g|webp|gif)$", re.I)


def _gateway_base_url() -> str:
    if os.environ.get("ANTHROPIC_BASE_URL"):
        return os.environ["ANTHROPIC_BASE_URL"].rstrip("/")
    try:
        conf = json.loads((Path.home() / ".claude/settings.json").read_text(encoding="utf-8"))
        url = str((conf.get("env") or {}).get("ANTHROPIC_BASE_URL") or "").strip()
        return url.rstrip("/")
    except (OSError, ValueError):
        return ""


def _parse_json_obj(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    m = JSON_FENCE_RE.search(text)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    return None


def _image_blocks(raw_images: list) -> list[dict]:
    blocks: list[dict] = []
    if not isinstance(raw_images, list):
        return blocks
    for entry in raw_images[:4]:
        if not isinstance(entry, dict):
            continue
        m = DATA_IMG_RE.match(str(entry.get("data") or ""))
        if not m:
            continue
        media = "image/jpeg" if m.group(1) == "image/jpg" else m.group(1)
        b64 = re.sub(r"\s+", "", m.group(2))
        if len(b64) > 7_000_000:
            continue
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media, "data": b64},
        })
    return blocks


def _resolve_local_doc(path_str: str) -> Path | None:
    raw = str(path_str or "").strip()
    if not raw or raw.startswith(("http://", "https://")):
        return None
    cand = (settings.REPO / raw).resolve()
    if cand.is_file():
        return cand
    rel = re.sub(r"^(?:clients/[^/]+/)?(?:docs|scripts)/", "", raw).lstrip("/")
    for base in (settings.DOCS_DIR, settings.CLIENT_DIR):
        p = (base / rel).resolve()
        if p.is_file():
            return p
    return None


def _doc_urls(path_or_url: str) -> tuple[str, str]:
    """Return `(server_href, relative_fallback_href)` for an image/file."""
    raw = str(path_or_url or "").strip()
    if raw.startswith(("http://", "https://")):
        return raw, raw
    rel = re.sub(r"^(?:clients/[^/]+/)?(?:docs|scripts)/", "", raw).lstrip("/")
    name = rel or Path(raw).name
    return f"/doc/{name}", f"../docs/{name}"


def _ticket_visuals(ticket: dict) -> list[dict]:
    """Collect visual screenshots/diagrams attached to this ticket or its items."""
    seen: set[str] = set()
    out: list[dict] = []
    exp_vis = ((ticket.get("prep") or {}).get("explainer") or {}).get("visuals") or []
    candidates: list[dict] = [v for v in exp_vis if isinstance(v, dict)]
    for item in reversed(ticket.get("items") or []):
        for f in ((item.get("prepared") or {}).get("files") or []):
            if isinstance(f, dict):
                candidates.append(f)

    for f in candidates:
        raw = str(f.get("path") or f.get("url") or "").strip()
        if not raw or not IMG_EXT_RE.search(raw) or raw in seen:
            continue
        seen.add(raw)
        href, fallback = _doc_urls(raw)
        out.append({
            "raw_path": raw,
            "href": href,
            "fallback": fallback,
            "label": str(f.get("label") or Path(raw).name),
            "caption": str(f.get("caption") or f.get("where") or ""),
        })
    return out[:6]


def _auto_evidence_blocks(ticket: dict, max_imgs: int = 2) -> list[dict]:
    """Load up to `max_imgs` small local screenshots from the ticket so Opus can
    visually inspect the actual Kraken UI / PDF evidence when answering."""
    blocks: list[dict] = []
    total_bytes = 0
    for vis in _ticket_visuals(ticket):
        if len(blocks) >= max_imgs:
            break
        local = _resolve_local_doc(vis["raw_path"])
        if not local:
            continue
        try:
            data = local.read_bytes()
        except OSError:
            continue
        if not data or len(data) > 1_800_000 or (total_bytes + len(data)) > 2_800_000:
            continue
        ext = local.suffix.lower().lstrip(".")
        media = (
            "image/png" if ext == "png"
            else "image/webp" if ext == "webp"
            else "image/gif" if ext == "gif"
            else "image/jpeg"
        )
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media,
                "data": base64.b64encode(data).decode("ascii"),
            },
        })
        total_bytes += len(data)
    return blocks


def _board_links(board: dict) -> list[dict]:
    """Every ticket on the board by name and link, so an ask can link a ticket
    a draft names (a parent, a 件名 field value, a sibling) instead of saying it
    has no URL. Internal tickets are marked, because those never reach the client."""
    out: list[dict] = []
    for t in board.get("tickets") or []:
        if not isinstance(t, dict):
            continue
        url = str(t.get("asana_url") or "").strip()
        if url:
            out.append({
                "ref": t.get("ref"),
                "title_ja": t.get("title_ja"),
                "title_en": t.get("title_en"),
                "url": url,
            })
        it = t.get("internal_ticket")
        if isinstance(it, dict) and str(it.get("url") or "").strip():
            out.append({
                "ref": t.get("ref"),
                "title_en": it.get("name"),
                "url": str(it["url"]).strip(),
                "internal": True,
            })
    return out


LINK_RULE = (
    "- Links: when a draft names a ticket, a parent ticket, a field value that is a ticket's title "
    "(such as a 件名 like `219_...`) or a sibling task, look it up in `board_links` by `title_ja`, "
    "`title_en` or `ref` and use that `url`. Also look for URLs in the ticket's events, steps and "
    "prepared sources. Never answer that you have no link before checking all of these. Entries "
    "marked `internal` never go into a client-facing draft. Only when nothing matches, say exactly "
    "which name you searched for.\n"
)


def _ticket_context(ticket: dict, board: dict | None = None) -> dict:
    """Rich, self-contained ticket + items + prepared findings context so inline AI
    never answers blind or misses an engineer/client nuance."""
    items_ctx = []
    all_items = list(ticket.get("items") or [])
    open_items = [i for i in all_items if i.get("state") not in B.CLOSED_STATES]
    closed_items = [i for i in all_items if i.get("state") in B.CLOSED_STATES][-2:]
    for it in open_items + closed_items:
        prep = it.get("prepared") or {}
        items_ctx.append({
            "id": it.get("id"),
            "state": it.get("state"),
            "title": it.get("title"),
            "where_it_stands": it.get("where_it_stands"),
            "steps": it.get("steps"),
            "done_when": it.get("done_when"),
            "progress_note": it.get("progress_note"),
            "waits_on": it.get("waits_on"),
            "prepared": {
                "conclusion": prep.get("conclusion"),
                "findings": (prep.get("findings") or [])[:5],
                "unanswered": prep.get("unanswered"),
                "notes": (prep.get("notes") or [])[:3],
                "files": [
                    {"label": f.get("label"), "path": f.get("path") or f.get("url"), "where": f.get("where")}
                    for f in (prep.get("files") or [])[:6]
                    if isinstance(f, dict)
                ],
                "sources": (prep.get("sources") or [])[:4],
            } if prep else None,
            "unsent_drafts": [
                {
                    "draft_number": idx,
                    "target": d.get("target"),
                    "language": d.get("language"),
                    "body_ruby": d.get("body_ruby"),
                    "body_en": d.get("body_en") or d.get("body"),
                }
                for idx, d in enumerate(B.drafts(it), 1)
                if B.unsent(d)
            ],
        })

    out = {
        "ticket_ref": ticket.get("ref"),
        "title_en": ticket.get("title_en"),
        "title_ja": ticket.get("title_ja"),
        "asana_url": ticket.get("asana_url"),
        "asana": ticket.get("asana"),
        "where_it_stands": ticket.get("where_it_stands"),
        "internal_ticket": ticket.get("internal_ticket"),
        "closes_when": ticket.get("closes_when"),
        "terms": (ticket.get("terms") or [])[:8],
        "recent_events": (ticket.get("events") or [])[-10:],
        "prep": ticket.get("prep"),
        "items": items_ctx,
    }
    if board is not None:
        out["board_links"] = _board_links(board)
    return out


def _call_llm(
    system_prompt: str,
    user_prompt: str,
    model_id: str,
    images: list | None = None,
    extra_blocks: list[dict] | None = None,
) -> tuple[str, str]:
    """Return `(raw_text, error)`. Uses direct HTTPS `/v1/messages` when possible,
    with automatic fallback to `claude --print --strict-mcp-config --tools ""`."""
    kind = agent.pick("claude")
    if kind is None:
        return "", "Claude Code is not configured on this Mac."

    api_key = kind.credential() or kind.wait_for_credential(patience=25)
    if not api_key:
        return "", "Could not read your model key from 1Password. Unlock 1Password and try again."

    base_url = _gateway_base_url()
    img_blocks = _image_blocks(images or []) + (extra_blocks or [])

    if base_url:
        content: list[dict] = [*img_blocks[:4], {"type": "text", "text": user_prompt}]
        payload = json.dumps({
            "model": model_id,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": content}],
        }).encode("utf-8")
        for attempt in range(2):
            req = urllib.request.Request(
                f"{base_url}/v1/messages",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=55) as resp:
                    resp_obj = json.loads(resp.read().decode("utf-8"))
                parts = [
                    str(p.get("text") or "")
                    for p in (resp_obj.get("content") or [])
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                out_text = "".join(parts).strip()
                if out_text:
                    return out_text, ""
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403) and attempt == 0:
                    kind.forget_credential()
                    api_key = kind.wait_for_credential(patience=20, fresh=True)
                    if api_key:
                        continue
                break
            except (OSError, ValueError):
                break

    cmd = [
        kind.binary, "--print", "--model", model_id,
        "--tools", "",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--setting-sources", "user",
        "--settings", json.dumps({
            "apiKeyHelper": "printenv ANTHROPIC_API_KEY",
            "env": {"CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "0"},
        }),
    ]
    combined = f"{system_prompt}\n\n---\n\n{user_prompt}\n"
    try:
        proc = subprocess.run(
            cmd, input=combined, text=True, capture_output=True,
            cwd="/tmp", env=kind.env(), timeout=65, check=False,
        )
    except subprocess.TimeoutExpired:
        return "", "The model timed out after 65s."
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[-240:]
        return "", f"Model call failed: {err}"
    return proc.stdout.strip(), ""


def _render_draft_payload(board_data: dict, item_id: str, draft_num: int) -> dict:
    glossed = render.apply_glossary(copy.deepcopy(board_data))
    _, g_item = B.by_id(glossed, item_id)
    if not g_item:
        return {}
    g_drafts = B.drafts(g_item)
    if not (1 <= draft_num <= len(g_drafts)):
        return {}
    d = g_drafts[draft_num - 1]
    is_ja = B.is_ja(d)
    link_url = str(d.get("link") or "")
    keep_links = (
        "canvas" in str(d.get("target") or "").lower()
        or "/docs/" in link_url
        or (not is_ja and "slack.com" in link_url)
    )
    if is_ja:
        raw_body = d.get("body_ruby") or d.get("body_ja") or d.get("body", "")
        body_html = render.draft_html(raw_body, ruby=True, keep_links=keep_links)
        en_html = render.draft_html(d.get("body_en"), keep_links=keep_links) if d.get("body_en") else ""
    else:
        raw_body = d.get("body_en") or d.get("body_ruby") or d.get("body", "")
        body_html = render.draft_html(raw_body, ruby=False, keep_links=keep_links)
        en_html = ""
    return {
        "is_ja": is_ja,
        "draft_body_html": body_html,
        "draft_en_html": en_html,
        "copy_plain": render.draft_plain(raw_body),
        "copy_html": render.draft_html(raw_body, ruby=False, keep_links=keep_links),
    }


def _render_script_payload(board_data: dict, ticket_ref: str) -> dict:
    glossed = render.apply_glossary(copy.deepcopy(board_data))
    t = next((x for x in glossed.get("tickets", []) if str(x.get("ref")) == ticket_ref), None)
    if not t:
        return {}
    blocks = (t.get("prep") or {}).get("script") or []
    return {"script_html": render.render_script(blocks)}


def _validate_candidate(b: dict) -> list[str]:
    problems = list(B.check(b))
    for _name, fn in plugins.board_checks():
        try:
            res = fn(b)
        except TypeError:
            res = fn()
        if res:
            problems.extend(res)
    if settings.readings():
        bare_kanji = re.compile(r"[一-龯々〆ヵヶ]{3,}")
        ok_bare = set(settings.language().get("plain_runs") or []) | {
            "対応中", "対応済", "確認中", "確認済", "未対応", "未確認", "今日中", "大丈夫"
        }
        r_terms = tuple(str(x) for x in (settings.language().get("reading_terms") or []) if x)
        for t in b.get("tickets", []):
            prep = t.get("prep") or {}
            for blk in (prep.get("script") or []):
                for ln in (blk.get("lines") or []):
                    stripped = re.sub(r"\{[^|{}]+\|[^|{}]+\}", "", str(ln.get("ja_ruby") or ""))
                    for m in bare_kanji.finditer(stripped):
                        if m.group(0) not in ok_bare:
                            problems.append(
                                f"spoken line on {t.get('ref')} has bare kanji compound {m.group(0)!r} without {{word|reading}} markup"
                            )
                    for term in r_terms:
                        if term in stripped:
                            problems.append(
                                f"spoken line on {t.get('ref')} has bare reading term {term!r} without {{word|reading}} markup"
                            )
            for qa in ((prep.get("explainer") or {}).get("if_asked") or []):
                if isinstance(qa, dict) and qa.get("say_ja"):
                    stripped = re.sub(r"\{[^|{}]+\|[^|{}]+\}", "", str(qa.get("say_ja") or ""))
                    for m in bare_kanji.finditer(stripped):
                        if m.group(0) not in ok_bare:
                            problems.append(
                                f"explainer if_asked on {t.get('ref')} has bare kanji compound {m.group(0)!r} without {{word|reading}} markup"
                            )
                    for term in r_terms:
                        if term in stripped:
                            problems.append(
                                f"explainer if_asked on {t.get('ref')} has bare reading term {term!r} without {{word|reading}} markup"
                            )
    return problems


def _record_undo(snap: dict) -> str:
    token = secrets.token_urlsafe(10)
    with _undo_lock:
        _undos[token] = snap
        while len(_undos) > MAX_UNDOS:
            oldest = next(iter(_undos))
            _undos.pop(oldest, None)
    return token


def undo(token: str) -> tuple[int, dict]:
    with _undo_lock:
        snap = _undos.pop(str(token or ""), None)
    if not snap:
        return 404, {"error": "nothing to undo (already undone or server restarted)"}

    b = B.load()
    kind = snap["kind"]
    if kind == "draft":
        _, item = B.by_id(b, snap["item_id"])
        if not item:
            return 404, {"error": "job not found"}
        ds = B.drafts(item)
        dn = snap["draft_num"]
        if not (1 <= dn <= len(ds)):
            return 404, {"error": "draft not found"}
        target_d = ds[dn - 1]
        for k in ("body_ruby", "body_en", "body"):
            if k in snap["before"] and snap["before"][k] is not None:
                target_d[k] = snap["before"][k]
            elif k in snap["before"]:
                target_d.pop(k, None)
        B.save(b)
        T.rebuild()
        return 200, {
            "undone": True,
            "summary": f"Restored previous Draft {dn}",
            **_render_draft_payload(b, snap["item_id"], dn),
        }

    if kind == "script":
        t = next((x for x in b.get("tickets", []) if str(x.get("ref")) == snap["ticket_ref"]), None)
        if not t or not isinstance(t.get("prep"), dict):
            return 404, {"error": "ticket prep not found"}
        t["prep"]["script"] = snap["before_script"]
        B.save(b)
        T.rebuild()
        return 200, {
            "undone": True,
            "summary": "Restored previous speaking script",
            **_render_script_payload(b, snap["ticket_ref"]),
        }

    if kind == "ticket":
        t = next((x for x in b.get("tickets", []) if str(x.get("ref")) == snap["ticket_ref"]), None)
        if not t or not isinstance(t.get("prep"), dict):
            return 404, {"error": "ticket prep not found"}
        if snap.get("before_explainer") is None:
            t["prep"].pop("explainer", None)
        else:
            t["prep"]["explainer"] = snap["before_explainer"]
        B.save(b)
        T.rebuild()
        return 200, {"undone": True, "summary": "Restored previous walkthrough", "reload": True}

    if kind == "job":
        _, item = B.by_id(b, snap["item_id"])
        if not item:
            return 404, {"error": "job not found"}
        for k, v in snap["before"].items():
            if v is None:
                item.pop(k, None)
            else:
                item[k] = v
        B.save(b)
        T.rebuild()
        return 200, {"undone": True, "summary": f"Restored Job {snap['item_id']}", "reload": True}

    return 400, {"error": "unknown undo kind"}


def _clean_em_dashes(text: str, ja: bool = False) -> str:
    if not text or "—" not in text:
        return text
    return text.replace(" — ", "：" if ja else ", ").replace("—", "・" if ja else ", ")


ANSWER_SCHEMA_FIELDS = (
    '  "headline": "When action==\'answer\': 1 crisp bottom-line sentence answering directly (active voice, <=20 words).",\n'
    '  "bullets": ["When action==\'answer\': 2 to 4 short bullets (`**Label:** plain explanation`). Decode every Japanese/domain term, name who does what and on which screen/place, and call out plainly if the current draft/script has an error or ambiguity."],\n'
    '  "flow": [ { "who": "You · PROD Support Site", "step": "1. Short label", "detail": "Concrete action in plain words" } ],\n'
    '  "show_files": true,\n'
    '  "suggested_prompt": "Optional: ONLY when action==\'answer\' AND you spot a flaw, ambiguity, or better phrasing in the current text, write a 1-sentence edit instruction the owner can apply in 1 click (otherwise empty string)"'
)


def _format_answer_response(parsed: dict, ticket: dict) -> dict:
    """Build a rich, visual HTML + structured answer payload from `parsed`."""
    headline = _clean_em_dashes(str(parsed.get("headline") or parsed.get("summary") or "").strip())
    raw_bullets = parsed.get("bullets") if isinstance(parsed.get("bullets"), list) else []
    bullets = [_clean_em_dashes(str(x).strip()) for x in raw_bullets if str(x).strip()]
    if not headline and bullets:
        headline = bullets.pop(0)

    md_parts = []
    if headline:
        md_parts.append(f"**{headline}**")
    if bullets:
        # Strip only a list marker ("- " / "* "), never the first star of a
        # `**Label:**` bold, which used to leave "*Label:<b>..." on the card.
        md_parts.append("\n".join("- " + re.sub(r"^[-*•]\s+", "", b) for b in bullets))
    answer_md = "\n\n".join(md_parts)
    answer_html = render.answered(answer_md) if answer_md else ""

    flow_out = []
    for f in (parsed.get("flow") or [])[:4]:
        if isinstance(f, dict) and (f.get("step") or f.get("detail")):
            flow_out.append({
                "who": _clean_em_dashes(str(f.get("who") or "").strip()),
                "step": _clean_em_dashes(str(f.get("step") or "").strip()),
                "detail": _clean_em_dashes(str(f.get("detail") or "").strip()),
            })

    visuals = []
    if parsed.get("show_files"):
        for v in _ticket_visuals(ticket)[:4]:
            visuals.append({
                "href": v["href"],
                "fallback": v["fallback"],
                "label": v["label"],
                "caption": v["caption"],
            })

    suggested = _clean_em_dashes(str(parsed.get("suggested_prompt") or "").strip())
    plain_summary = headline + (
        " " + " ".join(re.sub(r"\*\*([^*]+)\*\*", r"\1", b) for b in bullets[:2])
        if bullets else ""
    )
    return {
        "action": "answer",
        "summary": plain_summary.strip() or "Answered.",
        "headline": headline,
        "answer_html": answer_html,
        "flow": flow_out,
        "files": visuals,
        "suggested_prompt": suggested,
    }


# Every exchange in an Ask/Revise tray, kept on disk so the conversation is
# still there after the page reloads, the tray is closed, or the server
# restarts. Not in `render.SOURCES`: a new turn must not reload the page.
_hist_lock = threading.Lock()
MAX_HISTORY = 400
MAX_PRIOR_CHARS = 3200


def _history_file() -> Path:
    return settings.STATE_DIR / "quick-edit.json"


def _slot(kind: str, ref: str, draft) -> dict:
    kind = str(kind or "draft").strip()
    try:
        dn = int(draft or 1)
    except (TypeError, ValueError):
        dn = 1
    return {"kind": kind, "ref": str(ref or "").strip(), "draft": dn if kind == "draft" else 0}


def _same_slot(turn: dict, slot: dict) -> bool:
    return (
        turn.get("kind") == slot["kind"]
        and str(turn.get("ref") or "") == slot["ref"]
        and int(turn.get("draft") or 0) == slot["draft"]
    )


def _load_turns() -> list[dict]:
    try:
        data = json.loads(_history_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    turns = data.get("turns") if isinstance(data, dict) else None
    return [t for t in turns if isinstance(t, dict)] if isinstance(turns, list) else []


def _save_turns(turns: list[dict]) -> None:
    f = _history_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"turns": turns[-MAX_HISTORY:]}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, f)


def history(kind: str, ref: str, draft=1) -> list[dict]:
    """Turns for one tray, oldest first."""
    slot = _slot(kind, ref, draft)
    with _hist_lock:
        return [t for t in _load_turns() if _same_slot(t, slot)]


def clear_history(kind: str, ref: str, draft=1) -> int:
    slot = _slot(kind, ref, draft)
    with _hist_lock:
        turns = _load_turns()
        keep_turns = [t for t in turns if not _same_slot(t, slot)]
        _save_turns(keep_turns)
    return len(turns) - len(keep_turns)


def _prior_text(turns: list[dict]) -> str:
    parts = []
    for t in turns:
        if t.get("action") == "error":
            continue
        q = (f"Excerpt: {t['quote']}\n" if t.get("quote") else "") + f"User: {t.get('instruction', '')}"
        a = str(t.get("summary") or "")
        if t.get("action") == "edit":
            a = ("(edited, later undone) " if t.get("undone_at") else "(edited) ") + a
        parts.append(f"{q}\nAssistant: {a}")
    return "\n\n".join(parts)[-MAX_PRIOR_CHARS:]


def _record(body: dict, code: int, reply: dict, tier: str) -> dict:
    """Append one exchange to the store and return the turn as the tray draws it."""
    slot = _slot(body.get("kind"), body.get("ref"), body.get("draft"))
    ok = code == 200
    action = str(reply.get("action") or "answer") if ok else "error"
    turn = {
        "id": secrets.token_hex(4),
        "at": settings.now().isoformat(timespec="seconds"),
        **slot,
        "tier": tier,
        "instruction": str(body.get("instruction") or "").strip()[:4000],
        "quote": str(body.get("quote") or "").strip()[:400],
        "images": len(body.get("images") or []) if isinstance(body.get("images"), list) else 0,
        "action": action,
        "summary": str(reply.get("summary") or reply.get("error") or "").strip()[:2000],
    }
    if action == "answer":
        for k in ("answer_html", "flow", "files", "suggested_prompt"):
            if reply.get(k):
                turn[k] = reply[k]
    if action == "edit" and reply.get("undo_id"):
        turn["undo_id"] = reply["undo_id"]
    with _hist_lock:
        turns = _load_turns()
        turns.append(turn)
        _save_turns(turns)
    return turn


def _mark_undone(token: str) -> None:
    with _hist_lock:
        turns = _load_turns()
        for t in reversed(turns):
            if t.get("undo_id") == token:
                t["undone_at"] = settings.now().isoformat(timespec="seconds")
                _save_turns(turns)
                return


def run(body: dict) -> tuple[int, dict]:
    """Answer or edit, then keep the exchange so the tray can show it again.

    `{"history": true, kind, ref, draft}` returns that tray's saved turns, and
    `{"clear_history": true, ...}` forgets them. Everything else is a question,
    an edit or an undo; the conversation so far is read from the store, so a
    follow-up after a reload still knows what was said.
    """
    if body.get("history"):
        return 200, {"turns": history(body.get("kind"), body.get("ref"), body.get("draft"))}
    if body.get("clear_history"):
        return 200, {"cleared": clear_history(body.get("kind"), body.get("ref"), body.get("draft"))}
    if body.get("undo"):
        token = str(body["undo"])
        code, reply = undo(token)
        if code == 200:
            _mark_undone(token)
        return code, reply

    if not str(body.get("prior") or "").strip():
        past = history(body.get("kind"), body.get("ref"), body.get("draft"))
        if past:
            body = {**body, "prior": _prior_text(past)}
    tier = "sonnet" if str(body.get("tier") or "").strip().lower() == "sonnet" else "opus"
    try:
        code, reply = _run(body)
    except Exception as exc:  # noqa: BLE001 - the tray must still show what happened
        code, reply = 500, {"error": f"{type(exc).__name__}: {exc}"}
    if not (code == 400 and not str(body.get("instruction") or "").strip()):
        try:
            reply = {**reply, "turn": _record(body, code, reply, tier)}
        except OSError:
            pass
    return code, reply


def _run(body: dict) -> tuple[int, dict]:
    """Execute an inline AI question or surgical edit on `draft`, `script`, `ticket`, or `job`."""
    if body.get("undo"):
        return undo(str(body["undo"]))

    kind = str(body.get("kind") or "draft").strip()
    ref = str(body.get("ref") or "").strip()
    instruction = str(body.get("instruction") or "").strip()
    quote = str(body.get("quote") or "").strip()
    prior = str(body.get("prior") or "").strip()
    images = body.get("images") if isinstance(body.get("images"), list) else []
    try:
        draft_num = int(body.get("draft") or 1)
    except (TypeError, ValueError):
        draft_num = 1

    if not instruction and not images:
        return 400, {"error": "type a question or instruction, or click a preset"}
    if not instruction and images:
        instruction = "Inspect the attached screenshot(s) against this item and tell me plainly what it shows and whether anything needs changing."

    # Default to Opus 5.5 so domain reasoning and subtle checks are rock-solid.
    chosen_tier = str(body.get("tier") or "opus").strip().lower()
    model_id = (
        settings.model_id("sonnet", "5-5")
        if chosen_tier == "sonnet"
        else settings.model_id("opus", "5-5")
    )

    b = B.load()
    baseline_problems = set(_validate_candidate(b))

    try:
        profile_txt = settings.PROFILE.read_text(encoding="utf-8").strip()
    except OSError:
        profile_txt = ""

    sys_prompt = (
        "You are DANDORI's senior inline assistant and surgical editor.\n"
        "Follow ALL house-style, domain, and confidentiality rules in the owner's profile below:\n"
        "- Never use em dashes (—) in any output.\n"
        "- Never include AI disclaimers such as '※AIを使って…' unless explicitly asked.\n"
        "- Client-facing lines must never contain story points, t-shirt sizes, queue positions, internal Asana URLs, KT numbers, 'CE', or Kraken engineer names.\n"
        + ("- Japanese `body_ruby`, `ja_ruby`, and `say_ja` lines MUST wrap kanji compounds above N3 as whole-word `{漢字|かんじ}` readings (never per-character).\n" if settings.readings() else "")
        + "- Technical drafts to Kraken engineers (CE/IE) follow ask-don't-propose.\n"
        + LINK_RULE
        + "- A draft posted as an Asana comment is read inside that ticket, under its title and earlier comments: never open by naming the ticket or restating its subject ('〇〇の件ですが', 'Regarding the X'), never retell an earlier comment there unless it is weeks old; open with the news. Keep (or add) the @mentions of the people who need to act or know. Slack and spoken lines still say what they are about.\n"
        "- When answering a question (`action == \"answer\"`), be pedagogical, concrete, and honest:\n"
        "  1. Active voice, <= 20 words per sentence, one idea per sentence, zero unexplained jargon.\n"
        "  2. Always name WHO acts (You, the client's owner, Kraken engineer) and WHERE (which screen, button, PDF box, or Slack canvas).\n"
        "  3. Check the ticket's `events`, `prepared.findings`, and attached screenshots carefully: if a draft or script line has a factual flaw or confusing wording (for instance mixing up a PDF notice setting with a price/discount calculation), say so plainly and populate `suggested_prompt` so the owner can fix it in one click!\n"
        "  4. Include `flow` (2-4 visual steps) whenever explaining a process, timeline, or multi-step sequence, and set `show_files: true` whenever seeing the ticket's screenshots helps.\n"
        "- Return ONLY valid JSON matching the requested schema, with no prose outside the JSON.\n\n"
        f"## Owner Profile\n{profile_txt[:15000]}"
    )

    quote_block = f"Selected excerpt the owner highlighted:\n> {quote}\n\n" if quote else ""

    if kind == "draft":
        item_id = ref.partition(":")[2] if ":" in ref else ref
        ticket, item = B.by_id(b, item_id)
        if not ticket or not item:
            return 404, {"error": f"no job matching {ref}"}
        ds = B.drafts(item)
        if not (1 <= draft_num <= len(ds)):
            return 404, {"error": f"Draft {draft_num} does not exist on Job {item_id}"}
        d = ds[draft_num - 1]
        is_ja = B.is_ja(d)

        ctx = {
            "ticket": _ticket_context(ticket, b),
            "focused_job_id": item.get("id"),
            "focused_draft_number": draft_num,
            "focused_draft": d,
        }
        schema_note = (
            "{\n"
            '  "action": "edit" or "answer",\n'
            '  "summary": "When action==\'edit\': 1 short sentence saying what changed.",\n'
            + ('  "body_ruby": "Full updated Japanese text with {漢字|かんじ} readings (required when action==\'edit\')",\n' if is_ja else "")
            + '  "body_en": "Full updated English translation (when Japanese) or full English draft (required when action==\'edit\')",\n'
            + ANSWER_SCHEMA_FIELDS + "\n}"
        )
        u_prompt = (
            f"Context JSON:\n```json\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n```\n\n"
            + (f"Earlier exchange in this box:\n{prior}\n\n" if prior else "")
            + quote_block
            + f"Owner's request on Draft {draft_num}:\n{instruction}\n\n"
            + "If the owner asks to revise, rewrite, shorten, soften, or change the draft, set `action` to `\"edit\"` and return the complete updated draft text. "
            + "If the owner is asking a question (e.g. 'what does this mean?', 'is this right?', 'explain', 'check facts'), set `action` to `\"answer\"`.\n"
            + f"Return ONLY valid JSON with this shape:\n{schema_note}"
        )

        ev_imgs = _auto_evidence_blocks(ticket, max_imgs=2) if not images else []
        raw, err = _call_llm(sys_prompt, u_prompt, model_id, images=images, extra_blocks=ev_imgs)
        if err:
            return 502, {"error": err}
        parsed = _parse_json_obj(raw)
        if not parsed:
            return 502, {"error": "Could not parse AI response as JSON. Try again."}

        if parsed.get("action") == "answer":
            return 200, _format_answer_response(parsed, ticket)

        b_fresh = B.load()
        _, f_item = B.by_id(b_fresh, item_id)
        if not f_item:
            return 404, {"error": "job disappeared"}
        f_ds = B.drafts(f_item)
        f_d = f_ds[draft_num - 1]
        before_snap = {
            "body_ruby": f_d.get("body_ruby"),
            "body_en": f_d.get("body_en"),
            "body": f_d.get("body"),
        }

        if is_ja:
            new_ruby = _clean_em_dashes(str(parsed.get("body_ruby") or "").strip(), ja=True)
            new_en = _clean_em_dashes(str(parsed.get("body_en") or "").strip(), ja=False)
            if not new_ruby:
                return 502, {"error": "Model returned empty Japanese draft."}
            f_d["body_ruby"] = new_ruby
            f_d["language"] = "ja"
            if new_en or f_d.get("body_en"):
                f_d["body_en"] = new_en or f_d.get("body_en", "")
        else:
            new_en = _clean_em_dashes(str(parsed.get("body_en") or parsed.get("body_ruby") or "").strip(), ja=False)
            if not new_en:
                return 502, {"error": "Model returned empty draft."}
            if "body_en" in f_d or not f_d.get("body"):
                f_d["body_en"] = new_en
            else:
                f_d["body"] = new_en

        new_problems = [p for p in _validate_candidate(b_fresh) if p not in baseline_problems]
        if new_problems:
            fix_prompt = (
                f"{u_prompt}\n\nYour previous JSON failed these validation checks:\n"
                + "\n".join(f"- {p}" for p in new_problems)
                + "\nFix every issue above and return ONLY the corrected JSON."
            )
            raw2, err2 = _call_llm(sys_prompt, fix_prompt, model_id)
            parsed2 = _parse_json_obj(raw2) if not err2 else None
            if parsed2:
                if is_ja and parsed2.get("body_ruby"):
                    f_d["body_ruby"] = _clean_em_dashes(str(parsed2["body_ruby"]).strip(), ja=True)
                    if parsed2.get("body_en"):
                        f_d["body_en"] = _clean_em_dashes(str(parsed2["body_en"]).strip(), ja=False)
                elif not is_ja and parsed2.get("body_en"):
                    f_d["body_en"] = _clean_em_dashes(str(parsed2["body_en"]).strip(), ja=False)
                parsed = parsed2
                new_problems = [p for p in _validate_candidate(b_fresh) if p not in baseline_problems]
            if new_problems:
                return 422, {"error": "Blocked by house-style check: " + "; ".join(new_problems[:2])}

        keep.keep("quick-edit")
        B.save(b_fresh)
        T.rebuild()
        undo_id = _record_undo({
            "kind": "draft",
            "item_id": str(item_id),
            "draft_num": draft_num,
            "before": before_snap,
        })
        return 200, {
            "action": "edit",
            "summary": _clean_em_dashes(str(parsed.get("summary") or "Updated draft.").strip()),
            "undo_id": undo_id,
            **_render_draft_payload(b_fresh, str(item_id), draft_num),
        }

    if kind == "script":
        t_ref = ref.partition(":")[2] if ":" in ref else ref
        ticket = next((x for x in b.get("tickets", []) if str(x.get("ref")) == t_ref), None)
        if not ticket or not isinstance(ticket.get("prep"), dict):
            return 404, {"error": f"no speaking prep on {t_ref}"}
        ctx = _ticket_context(ticket, b)
        u_prompt = (
            f"Context JSON:\n```json\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n```\n\n"
            + (f"Earlier exchange:\n{prior}\n\n" if prior else "")
            + quote_block
            + f"Owner's request on speaking script for {t_ref}:\n{instruction}\n\n"
            + "If the owner asks a question (e.g. what a line means, whether a claim is accurate, why we ask this), set `action` to `\"answer\"`. "
            + "If the owner asks to revise, simplify, shorten, or change what they say out loud, set `action` to `\"edit\"` and return the complete `script` list.\n"
            + "Return ONLY valid JSON:\n{\n"
            + '  "action": "edit" or "answer",\n'
            + '  "summary": "When action==\'edit\': 1 short sentence saying what changed",\n'
            + '  "script": [ { "heading": "現状", "heading_en": "Where it stands", "lines": [ { "ja_ruby": "...with {漢字|かんじ} readings...", "en": "...English..." } ] } ],\n'
            + ANSWER_SCHEMA_FIELDS + "\n}"
        )
        ev_imgs = _auto_evidence_blocks(ticket, max_imgs=2) if not images else []
        raw, err = _call_llm(sys_prompt, u_prompt, model_id, images=images, extra_blocks=ev_imgs)
        if err:
            return 502, {"error": err}
        parsed = _parse_json_obj(raw)
        if not parsed:
            return 502, {"error": "Could not parse AI response as JSON."}
        if parsed.get("action") == "answer":
            return 200, _format_answer_response(parsed, ticket)

        new_script = parsed.get("script")
        if not isinstance(new_script, list) or not new_script:
            return 502, {"error": "Model returned no speaking lines."}

        b_fresh = B.load()
        f_t = next((x for x in b_fresh.get("tickets", []) if str(x.get("ref")) == t_ref), None)
        if not f_t or not isinstance(f_t.get("prep"), dict):
            return 404, {"error": "ticket prep disappeared"}
        before_script = copy.deepcopy(f_t["prep"].get("script") or [])
        f_t["prep"]["script"] = new_script

        new_problems = [p for p in _validate_candidate(b_fresh) if p not in baseline_problems]
        if new_problems:
            fix_prompt = (
                f"{u_prompt}\n\nYour previous JSON failed these validation checks:\n"
                + "\n".join(f"- {p}" for p in new_problems)
                + "\nWrap every 3+ kanji compound and reading term in `{漢字|かんじ}` and remove any internal leak. Return ONLY valid JSON."
            )
            raw2, err2 = _call_llm(sys_prompt, fix_prompt, model_id)
            parsed2 = _parse_json_obj(raw2) if not err2 else None
            if parsed2 and isinstance(parsed2.get("script"), list):
                f_t["prep"]["script"] = parsed2["script"]
                parsed = parsed2
                new_problems = [p for p in _validate_candidate(b_fresh) if p not in baseline_problems]
            if new_problems:
                return 422, {"error": "Blocked by check: " + "; ".join(new_problems[:2])}

        keep.keep("quick-edit")
        B.save(b_fresh)
        T.rebuild()
        undo_id = _record_undo({
            "kind": "script",
            "ticket_ref": t_ref,
            "before_script": before_script,
        })
        return 200, {
            "action": "edit",
            "summary": _clean_em_dashes(str(parsed.get("summary") or "Updated speaking lines.").strip()),
            "undo_id": undo_id,
            **_render_script_payload(b_fresh, t_ref),
        }

    if kind == "ticket":
        t_ref = ref.partition(":")[2] if ":" in ref else ref
        ticket = next((x for x in b.get("tickets", []) if str(x.get("ref")) == t_ref), None)
        if not ticket:
            return 404, {"error": f"no ticket matching {t_ref}"}
        ctx = _ticket_context(ticket, b)
        u_prompt = (
            f"Context JSON:\n```json\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n```\n\n"
            + (f"Earlier exchange:\n{prior}\n\n" if prior else "")
            + quote_block
            + f"Owner's question or request on ticket {t_ref}:\n{instruction}\n\n"
            + "Default to `action: \"answer\"` unless the owner explicitly asks to rewrite/update the ticket's walkthrough (`explainer`). "
            + "When `action == \"edit\"`, return a complete, concrete, jargon-free `explainer` object (`badge`, `new_to_you`, `summary`, `why_say`, `flow` with `{step, who, where, detail}`, `journey`, `facts`, `if_asked`).\n"
            + "Return ONLY valid JSON:\n{\n"
            + '  "action": "answer" or "edit",\n'
            + '  "summary": "When action==\'edit\': 1 short sentence saying what changed",\n'
            + '  "explainer": { "badge": "...", "summary": "...", "why_say": "...", "flow": [{"step": "1. ...", "who": "You", "where": "PROD UI", "detail": "..."}], "journey": [...], "facts": [...], "if_asked": [...] },\n'
            + ANSWER_SCHEMA_FIELDS + "\n}"
        )
        ev_imgs = _auto_evidence_blocks(ticket, max_imgs=2) if not images else []
        raw, err = _call_llm(sys_prompt, u_prompt, model_id, images=images, extra_blocks=ev_imgs)
        if err:
            return 502, {"error": err}
        parsed = _parse_json_obj(raw)
        if not parsed:
            return 502, {"error": "Could not parse AI response as JSON."}
        if parsed.get("action") != "edit" or not isinstance(parsed.get("explainer"), dict):
            return 200, _format_answer_response(parsed, ticket)

        b_fresh = B.load()
        f_t = next((x for x in b_fresh.get("tickets", []) if str(x.get("ref")) == t_ref), None)
        if not f_t:
            return 404, {"error": "ticket disappeared"}
        f_t.setdefault("prep", {})
        before_exp = copy.deepcopy(f_t["prep"].get("explainer"))
        f_t["prep"]["explainer"] = parsed["explainer"]

        new_problems = [p for p in _validate_candidate(b_fresh) if p not in baseline_problems]
        if new_problems:
            fix_prompt = (
                f"{u_prompt}\n\nYour previous JSON failed these validation checks:\n"
                + "\n".join(f"- {p}" for p in new_problems)
                + "\nFix every issue above (wrap 3+ kanji compounds and reading terms in `{漢字|かんじ}` on `say_ja` and remove any internal leak). Return ONLY valid JSON."
            )
            raw2, err2 = _call_llm(sys_prompt, fix_prompt, model_id)
            parsed2 = _parse_json_obj(raw2) if not err2 else None
            if parsed2 and isinstance(parsed2.get("explainer"), dict):
                f_t["prep"]["explainer"] = parsed2["explainer"]
                parsed = parsed2
                new_problems = [p for p in _validate_candidate(b_fresh) if p not in baseline_problems]
            if new_problems:
                return 422, {"error": "Blocked by check: " + "; ".join(new_problems[:2])}

        keep.keep("quick-edit")
        B.save(b_fresh)
        T.rebuild()
        undo_id = _record_undo({
            "kind": "ticket",
            "ticket_ref": t_ref,
            "before_explainer": before_exp,
        })
        return 200, {
            "action": "edit",
            "summary": _clean_em_dashes(str(parsed.get("summary") or f"Updated walkthrough on {t_ref}.").strip()),
            "undo_id": undo_id,
            "reload": True,
        }

    if kind == "job":
        item_id = ref.partition(":")[2] if ":" in ref else ref
        ticket, item = B.by_id(b, item_id)
        if not ticket or not item:
            return 404, {"error": f"no job matching {ref}"}
        ctx = {
            "ticket": _ticket_context(ticket, b),
            "focused_job_id": item.get("id"),
        }
        u_prompt = (
            f"Context JSON:\n```json\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n```\n\n"
            + (f"Earlier exchange:\n{prior}\n\n" if prior else "")
            + quote_block
            + f"Owner's request on Job {item_id}:\n{instruction}\n\n"
            + "Default to `action: \"answer\"` when the owner asks a question or asks you to walk through how to do a step. "
            + "Set `action: \"edit\"` only when the owner asks to update/simplify `steps` or `where_it_stands` on the board.\n"
            + "Return ONLY valid JSON:\n{\n"
            + '  "action": "answer" or "edit",\n'
            + '  "summary": "When action==\'edit\': 1-sentence summary of what you edited",\n'
            + '  "where_it_stands": "Optional updated status string when action==\'edit\'",\n'
            + '  "steps": ["Optional updated list of actionable steps when action==\'edit\'"],\n'
            + ANSWER_SCHEMA_FIELDS + "\n}"
        )
        ev_imgs = _auto_evidence_blocks(ticket, max_imgs=2) if not images else []
        raw, err = _call_llm(sys_prompt, u_prompt, model_id, images=images, extra_blocks=ev_imgs)
        if err:
            return 502, {"error": err}
        parsed = _parse_json_obj(raw)
        if not parsed:
            return 502, {"error": "Could not parse AI response as JSON."}
        if parsed.get("action") != "edit":
            return 200, _format_answer_response(parsed, ticket)

        b_fresh = B.load()
        _, f_item = B.by_id(b_fresh, item_id)
        if not f_item:
            return 404, {"error": "job disappeared"}
        before_job = {
            "where_it_stands": copy.deepcopy(f_item.get("where_it_stands")),
            "steps": copy.deepcopy(f_item.get("steps")),
        }
        if isinstance(parsed.get("where_it_stands"), str) and parsed["where_it_stands"].strip():
            f_item["where_it_stands"] = _clean_em_dashes(parsed["where_it_stands"].strip())
        if isinstance(parsed.get("steps"), list) and parsed["steps"]:
            f_item["steps"] = [_clean_em_dashes(str(s).strip()) for s in parsed["steps"] if str(s).strip()]

        new_problems = [p for p in _validate_candidate(b_fresh) if p not in baseline_problems]
        if new_problems:
            fix_prompt = (
                f"{u_prompt}\n\nYour previous JSON failed these validation checks:\n"
                + "\n".join(f"- {p}" for p in new_problems)
                + "\nFix every issue above and return ONLY the corrected JSON."
            )
            raw2, err2 = _call_llm(sys_prompt, fix_prompt, model_id)
            parsed2 = _parse_json_obj(raw2) if not err2 else None
            if parsed2:
                if isinstance(parsed2.get("where_it_stands"), str) and parsed2["where_it_stands"].strip():
                    f_item["where_it_stands"] = _clean_em_dashes(parsed2["where_it_stands"].strip())
                if isinstance(parsed2.get("steps"), list) and parsed2["steps"]:
                    f_item["steps"] = [_clean_em_dashes(str(s).strip()) for s in parsed2["steps"] if str(s).strip()]
                parsed = parsed2
                new_problems = [p for p in _validate_candidate(b_fresh) if p not in baseline_problems]
            if new_problems:
                return 422, {"error": "Blocked by check: " + "; ".join(new_problems[:2])}

        keep.keep("quick-edit")
        B.save(b_fresh)
        T.rebuild()
        undo_id = _record_undo({
            "kind": "job",
            "item_id": str(item_id),
            "before": before_job,
        })
        return 200, {
            "action": "edit",
            "summary": _clean_em_dashes(str(parsed.get("summary") or f"Updated Job {item_id}.").strip()),
            "undo_id": undo_id,
            "reload": True,
        }

    return 400, {"error": f"unknown quick-edit kind: {kind}"}
