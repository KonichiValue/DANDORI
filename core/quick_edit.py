#!/usr/bin/env python3
"""Fast, surgical in-app AI revisions and answers for drafts, scripts, and jobs.

Unlike a full sweep or multi-turn Cursor investigation, a quick draft tweak on
the desk ("make this shorter", "remove CC to Yoshida-san", "say Friday instead")
already has the ticket, job, draft, glossary, and house-style profile in local
memory. Calling the Kraken LiteLLM `/v1/messages` endpoint directly (falling
back to `claude --print --strict-mcp-config --tools ""`) skips MCP startup and
multi-turn Bash edits, returning an in-place HTML swap + instant Undo in ~3–7s.
"""

from __future__ import annotations

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


def _call_llm(system_prompt: str, user_prompt: str, model_id: str, images: list | None = None) -> tuple[str, str]:
    """Return `(raw_text, error)`. Uses direct HTTPS `/v1/messages` when possible,
    with automatic fallback to `claude --print --strict-mcp-config --tools ""`."""
    kind = agent.pick("claude")
    if kind is None:
        return "", "Claude Code is not configured on this Mac."

    api_key = kind.credential() or kind.wait_for_credential(patience=25)
    if not api_key:
        return "", "Could not read your model key from 1Password. Unlock 1Password and try again."

    base_url = _gateway_base_url()
    img_blocks = _image_blocks(images or [])

    if base_url:
        content: list[dict] = [*img_blocks, {"type": "text", "text": user_prompt}]
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
                with urllib.request.urlopen(req, timeout=45) as resp:
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

    # Fallback: zero-MCP headless CLI call (`--strict-mcp-config` skips all MCP servers).
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
            cwd="/tmp", env=kind.env(), timeout=60, check=False,
        )
    except subprocess.TimeoutExpired:
        return "", "The model timed out after 60s."
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
    is_ja = d.get("language") == "ja"
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
    # Check bare 3+ kanji runs on spoken script lines when readings are enabled.
    if settings.readings():
        bare_kanji = re.compile(r"[一-龯々〆ヵヶ]{3,}")
        ok_bare = {"対応中", "対応済", "確認中", "確認済", "未対応", "未確認", "今日中", "大丈夫"}
        for t in b.get("tickets", []):
            for blk in ((t.get("prep") or {}).get("script") or []):
                for ln in (blk.get("lines") or []):
                    stripped = re.sub(r"\{[^|{}]+\|[^|{}]+\}", "", str(ln.get("ja_ruby") or ""))
                    for m in bare_kanji.finditer(stripped):
                        if m.group(0) not in ok_bare:
                            problems.append(
                                f"spoken line on {t.get('ref')} has bare kanji compound {m.group(0)!r} without {{word|reading}} markup"
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


def run(body: dict) -> tuple[int, dict]:
    """Execute a quick in-place edit or answer on `draft`, `script`, or `job`."""
    if body.get("undo"):
        return undo(str(body["undo"]))

    kind = str(body.get("kind") or "draft").strip()
    ref = str(body.get("ref") or "").strip()
    instruction = str(body.get("instruction") or "").strip()
    prior = str(body.get("prior") or "").strip()
    images = body.get("images") if isinstance(body.get("images"), list) else []
    try:
        draft_num = int(body.get("draft") or 1)
    except (TypeError, ValueError):
        draft_num = 1

    if not instruction and not images:
        return 400, {"error": "type an instruction or pick a preset"}
    if not instruction and images:
        instruction = "Inspect the attached screenshot(s) against this item and tell me if anything needs changing."

    chosen_tier = str(body.get("tier") or "sonnet").strip().lower()
    model_id = (
        settings.model_id("opus", "5-5")
        if chosen_tier == "opus"
        else settings.model_id("sonnet", "5-5")
    )

    b = B.load()
    baseline_problems = set(_validate_candidate(b))

    try:
        profile_txt = settings.PROFILE.read_text(encoding="utf-8").strip()
    except OSError:
        profile_txt = ""

    sys_prompt = (
        "You are DANDORI's surgical inline editor and assistant.\n"
        "Follow ALL house-style and confidentiality rules in the owner's profile below:\n"
        "- Never use em dashes (—) in any output.\n"
        "- Never include AI disclaimers such as '※AIを使って…' unless explicitly asked.\n"
        "- Client-facing lines must never contain story points, t-shirt sizes, queue positions, internal Asana URLs, KT numbers, 'CE', or Kraken engineer names.\n"
        + ("- Japanese `body_ruby` and `ja_ruby` lines MUST wrap kanji compounds above N3 as whole-word `{漢字|かんじ}` readings (never per-character).\n" if settings.readings() else "")
        + "- Technical drafts to Kraken engineers (CE/IE) follow ask-don't-propose (no raw code function/constant names unless an engineer wrote them).\n"
        "- Keep answers and edit summaries active, direct, and under 18 words per sentence.\n"
        "- Return ONLY valid JSON matching the requested schema, with no markdown prose outside the JSON.\n\n"
        f"## Owner Profile\n{profile_txt[:14000]}"
    )

    if kind == "draft":
        item_id = ref.partition(":")[2] if ":" in ref else ref
        ticket, item = B.by_id(b, item_id)
        if not ticket or not item:
            return 404, {"error": f"no job matching {ref}"}
        ds = B.drafts(item)
        if not (1 <= draft_num <= len(ds)):
            return 404, {"error": f"Draft {draft_num} does not exist on Job {item_id}"}
        d = ds[draft_num - 1]
        is_ja = d.get("language") == "ja" or bool(d.get("body_ruby"))

        ctx = {
            "ticket_ref": ticket.get("ref"),
            "ticket_title_en": ticket.get("title_en"),
            "ticket_title_ja": ticket.get("title_ja"),
            "ticket_where_it_stands": ticket.get("where_it_stands"),
            "recent_events": (ticket.get("events") or [])[-6:],
            "job_id": item.get("id"),
            "job_title": item.get("title"),
            "job_where_it_stands": item.get("where_it_stands"),
            "job_steps": item.get("steps"),
            "prepared_conclusion": (item.get("prepared") or {}).get("conclusion"),
            "prepared_findings": ((item.get("prepared") or {}).get("findings") or [])[:5],
            "draft_number": draft_num,
            "draft": d,
        }
        schema_note = (
            '{\n'
            '  "action": "edit" or "answer",\n'
            '  "summary": "If edit: 1 short sentence saying what changed. If answer: concise direct answer (1-3 short sentences).",\n'
            + ('  "body_ruby": "Full updated Japanese text with {漢字|かんじ} readings (required when action==\'edit\')",\n' if is_ja else "")
            + '  "body_en": "Full updated English translation (when Japanese) or full English draft (required when action==\'edit\')"\n'
            '}'
        )
        u_prompt = (
            f"Context JSON:\n```json\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n```\n\n"
            + (f"Earlier exchange in this edit box:\n{prior}\n\n" if prior else "")
            + f"Owner's request on Draft {draft_num}:\n{instruction}\n\n"
            + f"If the owner is asking to revise, shorten, soften, translate, or change the draft, set `action` to `\"edit\"` and return the complete updated draft text. "
            + f"If the owner is only asking a question (e.g. 'why', 'check facts', 'is this accurate') without asking to change the text, set `action` to `\"answer\"`.\n"
            + f"Return ONLY valid JSON with this exact shape:\n{schema_note}"
        )

        raw, err = _call_llm(sys_prompt, u_prompt, model_id, images=images)
        if err:
            return 502, {"error": err}
        parsed = _parse_json_obj(raw)
        if not parsed:
            return 502, {"error": "Could not parse AI response as JSON. Try again."}

        if parsed.get("action") == "answer":
            return 200, {
                "action": "answer",
                "summary": _clean_em_dashes(str(parsed.get("summary") or "").strip()),
            }

        # Apply edit on a fresh board load and validate before saving.
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
            # One automatic self-repair pass if house-style / confidentiality check caught an issue.
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
        cur_script = ticket["prep"].get("script") or []
        ctx = {
            "ticket_ref": t_ref,
            "title_en": ticket.get("title_en"),
            "title_ja": ticket.get("title_ja"),
            "where_it_stands": ticket.get("where_it_stands"),
            "unknowns": ticket["prep"].get("unknowns"),
            "current_script": cur_script,
        }
        u_prompt = (
            f"Context JSON:\n```json\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n```\n\n"
            + (f"Earlier exchange:\n{prior}\n\n" if prior else "")
            + f"Owner's request on speaking script for {t_ref}:\n{instruction}\n\n"
            + 'Return ONLY valid JSON:\n{\n'
            + '  "action": "edit" or "answer",\n'
            + '  "summary": "1 short sentence saying what changed, or concise direct answer",\n'
            + '  "script": [ { "heading": "現状", "heading_en": "Where it stands", "lines": [ { "ja_ruby": "...with {漢字|かんじ} readings...", "en": "...English..." } ] } ]\n'
            + '}'
        )
        raw, err = _call_llm(sys_prompt, u_prompt, model_id, images=images)
        if err:
            return 502, {"error": err}
        parsed = _parse_json_obj(raw)
        if not parsed:
            return 502, {"error": "Could not parse AI response as JSON."}
        if parsed.get("action") == "answer":
            return 200, {
                "action": "answer",
                "summary": _clean_em_dashes(str(parsed.get("summary") or "").strip()),
            }
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
                + "\nWrap every 3+ kanji compound in `{漢字|かんじ}` and remove any internal leak. Return ONLY valid JSON."
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

    if kind == "job":
        item_id = ref.partition(":")[2] if ":" in ref else ref
        ticket, item = B.by_id(b, item_id)
        if not ticket or not item:
            return 404, {"error": f"no job matching {ref}"}
        trimmed_item = {k: v for k, v in item.items() if k != "history"}
        if isinstance(trimmed_item.get("prepared"), dict):
            trimmed_item["prepared"] = {k: v for k, v in trimmed_item["prepared"].items() if k != "earlier"}
        ctx = {
            "ticket_ref": ticket.get("ref"),
            "ticket_where_it_stands": ticket.get("where_it_stands"),
            "recent_events": (ticket.get("events") or [])[-6:],
            "job": trimmed_item,
        }
        u_prompt = (
            f"Context JSON:\n```json\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n```\n\n"
            + (f"Earlier exchange:\n{prior}\n\n" if prior else "")
            + f"Owner's request on Job {item_id}:\n{instruction}\n\n"
            + 'Return ONLY valid JSON:\n{\n'
            + '  "action": "answer" or "edit",\n'
            + '  "summary": "Concise direct answer (1-4 short sentences) OR 1-sentence summary of what you edited",\n'
            + '  "where_it_stands": "Optional updated status string when action==\'edit\'",\n'
            + '  "steps": ["Optional updated list of actionable steps when action==\'edit\'"]\n'
            + '}'
        )
        raw, err = _call_llm(sys_prompt, u_prompt, model_id, images=images)
        if err:
            return 502, {"error": err}
        parsed = _parse_json_obj(raw)
        if not parsed:
            return 502, {"error": "Could not parse AI response as JSON."}
        if parsed.get("action") != "edit":
            return 200, {
                "action": "answer",
                "summary": _clean_em_dashes(str(parsed.get("summary") or "").strip()),
            }

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
