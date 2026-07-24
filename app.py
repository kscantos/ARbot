from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
import os
import re
import glob
import json
import time
import random
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import numpy as np
import httpx
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

# logging
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("arbot")
logger.setLevel(logging.INFO)
_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "app.log"),
    maxBytes=5_000_000,
    backupCount=5,
)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s: %(message)s"
))
logger.addHandler(_handler)
logger.addHandler(logging.StreamHandler())

# config
LARK_APP_ID     = os.getenv("LARK_APP_ID", "")
LARK_APP_SECRET = os.getenv("LARK_APP_SECRET", "")
LARK_API_BASE   = "https://open.larksuite.com/open-apis"
LARK_HOST       = "https://casinoplus.sg.larksuite.com"
NOTIFY_CHAT_ID  = os.getenv("NOTIFY_CHAT_ID", "")
WIKI_NODE_TOKENS = [t for t in os.getenv("WIKI_NODE_TOKENS", "").split(",") if t]
DOCX_TOKENS      = [t for t in os.getenv("DOCX_TOKENS", "").split(",") if t]

# model + alert store
model           = SentenceTransformer("all-MiniLM-L6-v2")
alerts          : list = []   # [{doc, url, title, severity, tag, action}]
alert_titles    : list = []
alert_embeddings        = []
sources_loaded  : int = 0

# only H1 / H2 start a new alert section
HEADING_LEVEL = {3: 1, 4: 2}
BLOCK_FIELDS  = {
    2: "text", 3: "heading1", 4: "heading2", 5: "heading3", 6: "heading4",
    7: "heading5", 8: "heading6", 9: "heading7", 10: "heading8", 11: "heading9",
    12: "bullet", 13: "ordered", 14: "code", 15: "quote",
}

# chat replies
GREETING_WORDS   = {"hello", "hi", "hey", "yo", "sup", "hiya", "heya", "morning", "howdy"}
GREETING_REPLIES = ["whats up", "hello!", "hey there", "hi!", "yo", "sup"]

# custom replies
CUSTOM_REPLIES = {
    "erm":  ";p",
    "what": "what",
}

# show the full alert if confident; otherwise list candidates
SHOW_SIM = 0.45
# hide matches below this (noise)
MIN_SIM  = 0.35


# token
async def get_lark_token() -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{LARK_API_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET},
        )
        r.raise_for_status()
        return r.json()["tenant_access_token"]


# ping (round-trip to lark api)
async def measure_lark_ping() -> float:
    start = time.perf_counter()
    await get_lark_token()
    return (time.perf_counter() - start) * 1000


# wiki -> obj_token
async def fetch_wiki_obj_token(node_token: str, token: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{LARK_API_BASE}/wiki/v2/spaces/get_node",
            params={"token": node_token},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json().get("data", {}).get("node", {}).get("obj_token", "")


# docu blocks
async def fetch_doc_blocks(document_id: str, token: str) -> list:
    blocks     = []
    page_token = ""
    async with httpx.AsyncClient() as client:
        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            r = await client.get(
                f"{LARK_API_BASE}/docx/v1/documents/{document_id}/blocks",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            data = r.json().get("data", {})
            blocks.extend(data.get("items", []))
            page_token = data.get("page_token", "")
            if not data.get("has_more"):
                break
    return blocks


# block -> text
def block_to_text(block: dict) -> str:
    field = BLOCK_FIELDS.get(block.get("block_type"))
    if not field:
        return ""
    elements = (block.get(field) or {}).get("elements", []) or []
    out = []
    for el in elements:
        tr = el.get("text_run")
        if tr and tr.get("content"):
            out.append(tr["content"])
    return "".join(out)


# blocks -> sections (split on H1/H2)
def blocks_to_sections(blocks: list) -> list:
    sections = []
    cur      = None
    for b in blocks:
        txt = block_to_text(b)
        if b.get("block_type") in HEADING_LEVEL:
            if cur:
                sections.append(cur)
            cur = {"title": txt.strip(), "lines": []}
        elif cur and txt.strip():
            cur["lines"].append(txt)
    if cur:
        sections.append(cur)
    return [
        {"title": s["title"], "body": "\n".join(s["lines"]).strip()}
        for s in sections if s["title"]
    ]


# markdown -> sections (split on # / ##)
def md_to_sections(content: str) -> list:
    sections = []
    cur      = None
    for line in content.splitlines():
        m = re.match(r"^(#{1,2})\s+(.*)", line)
        if m:
            if cur:
                sections.append(cur)
            cur = {"title": m.group(2).strip(), "lines": []}
        elif cur:
            cur["lines"].append(line)
    if cur:
        sections.append(cur)
    return [
        {"title": s["title"], "body": "\n".join(s["lines"]).strip()}
        for s in sections if s["title"]
    ]


# trim action: drop everything from "Alert Example" onward
def strip_action(body: str) -> str:
    out = []
    for line in body.splitlines():
        if line.strip().lower().startswith("alert example"):
            break
        out.append(line)
    return "\n".join(out).strip()


# parse labeled fields, fall back to heuristics
def parse_fields(title: str, body: str) -> dict:
    severity = tag = action = ""
    for line in body.splitlines():
        low = line.strip().lower()
        if low.startswith("severity:"):
            severity = line.split(":", 1)[1].strip()
        elif low.startswith(("tag:", "who to tag:", "oncall:", "on call:")):
            tag = line.split(":", 1)[1].strip()
        elif low.startswith(("action:", "action to do:", "steps:")):
            action = line.split(":", 1)[1].strip()

    if not severity:
        head = title.strip().split(" ")[0].upper().strip(":-")
        if head in {"CRITICAL", "WARNING", "INFO", "ERROR", "ALERT", "NOTICE"}:
            severity = head
    if not action:
        action = body
    action = strip_action(action)
    return {"severity": severity, "tag": tag, "action": action}


# register sections into the alert index
def register_sections(doc_name: str, doc_url: str, sections: list):
    for s in sections:
        f = parse_fields(s["title"], s["body"])
        alerts.append({
            "doc":      doc_name,
            "url":      doc_url,
            "title":    s["title"],
            "severity": f["severity"],
            "tag":      f["tag"],
            "action":   f["action"],
        })
        alert_titles.append(s["title"])


# startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    global alert_embeddings, sources_loaded

    # local docs
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    for file_path in glob.glob(os.path.join(BASE_DIR, "alertdocs", "*.md")):
        with open(file_path) as f:
            content = f.read()
        secs = md_to_sections(content)
        register_sections(os.path.basename(file_path), "", secs)
        sources_loaded += 1

    # lark docs
    token = await get_lark_token()

    for node_token in WIKI_NODE_TOKENS:
        try:
            document_id = await fetch_wiki_obj_token(node_token, token)
            if not document_id:
                logger.warning(f"No obj_token for wiki node: {node_token}")
                continue
            blocks = await fetch_doc_blocks(document_id, token)
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to load wiki node {node_token}: {e.response.status_code} {e.response.text}")
            continue

        secs = blocks_to_sections(blocks)
        register_sections(node_token, f"{LARK_HOST}/wiki/{node_token}", secs)
        sources_loaded += 1
        logger.info(f"Loaded wiki:{node_token}: {len(secs)} alerts")

    for doc_token in DOCX_TOKENS:
        try:
            blocks = await fetch_doc_blocks(doc_token, token)
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to load docx {doc_token}: {e.response.status_code} {e.response.text}")
            continue

        secs = blocks_to_sections(blocks)
        register_sections(doc_token, f"{LARK_HOST}/docx/{doc_token}", secs)
        sources_loaded += 1
        logger.info(f"Loaded docx:{doc_token}: {len(secs)} alerts")

    # embeddings (over titles, normalized so scores are real cosine)
    if alert_titles:
        alert_embeddings = model.encode(alert_titles, normalize_embeddings=True)
        logger.info(f"Sources: {sources_loaded}  Alerts: {len(alerts)}")

    # notify a chat that the bot (re)started, if configured
    if NOTIFY_CHAT_ID:
        try:
            await send_lark_message(NOTIFY_CHAT_ID, "bot started/restarted")
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to send startup notification: {e.response.status_code} {e.response.text}")

    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# models
class ChatRequest(BaseModel):
    text:  str
    top_k: int = 5

class ChatResponse(BaseModel):
    query:   str
    results: list


# search (over alert titles only)
def search_alerts(query: str, top_k: int = 5) -> list:
    if len(alert_embeddings) == 0:
        return []
    q    = model.encode([query], normalize_embeddings=True)
    sims = np.dot(q, alert_embeddings.T)[0]
    idx  = np.argsort(sims)[-top_k:][::-1]
    return [{**alerts[i], "score": float(sims[i])} for i in idx]


# message
def clean_text(raw: str) -> str:
    text = raw.strip()
    for i in range(1, 11):
        text = text.replace(f"@_user_{i}", "")
    text = text.replace("@_all", "")
    return " ".join(text.split())


# chat reply
def chat_reply(text: str) -> str:
    low   = text.lower().strip(" !.?,")
    words = set(low.split())
    if low in CUSTOM_REPLIES:
        return CUSTOM_REPLIES[low]
    if not low or words & GREETING_WORDS:
        return random.choice(GREETING_REPLIES)
    return "???"


# clean up action steps (free, rule-based; no API, never invents text)
def clean_action(title: str, action: str) -> str:
    if not action or not action.strip():
        return action

    out = []
    for line in action.splitlines():
        line = line.strip()
        if not line:
            continue
        # strip existing bullet/number prefixes so we don't double them
        line = re.sub(r"^(\d+[.)]\s*|[-*•]\s*)", "", line)
        if not line:
            continue
        out.append(f"- {line}")

    return "\n".join(out) if out else action


# severity -> header color
def sev_color(sev: str) -> str:
    s = (sev or "").upper()
    if "CRITICAL" in s:
        return "red"
    if "WARN" in s:
        return "orange"
    if "INFO" in s:
        return "green"
    return "blue"


# card: full alert detail
def build_alert_card(query: str, results: list) -> dict:
    best = results[0]
    doc  = f"[{best['doc']}]({best['url']})" if best["url"] else best["doc"]

    head = "\n".join([
        f"**Query:** {query}",
        f"**Doc:** {doc}",
        f"**Alert:** {best['title']}",
        f"**Severity:** {best['severity'] or '-'}",
        f"**Who to tag:** {best['tag'] or '-'}",
    ])
    # rule-based cleaned action
    action = clean_action(best["title"], best["action"] or "")
    action = (action or "-")[:800]

    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": head}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**Action to do:**\n{action}"}},
    ]
    if len(results) > 1:
        others = [r for r in results[1:] if r["score"] >= MIN_SIM]
        if others:
            lines = "\n".join(f"- {r['title']} ({r['score']:.0%})" for r in others)
            elements.append({"tag": "hr"})
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**Other matches:**\n{lines}"}})

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": best["title"]},
                   "template": sev_color(best["severity"])},
        "elements": elements,
    }


# card: list of candidate titles (no detail until exact title)
def build_list_card(query: str, results: list) -> dict:
    lines = "\n".join(f"- {r['title']} ({r['score']:.0%})" for r in results)
    body  = f"**Query:** {query}\nNo exact alert match. Closest titles:"
    hint  = "Search the full title for details, e.g.\n/s " + results[0]["title"]
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "Possible matches"},
                   "template": "blue"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": lines}},
            {"tag": "div", "text": {"tag": "lark_md", "content": hint}},
        ],
    }


# card: nothing relevant
def build_none_card(query: str) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "No match"},
                   "template": "grey"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
                "content": f"**Query:** {query}\nNo matching alert found."}},
        ],
    }


# decide which card to send
def build_search_card(query: str, results: list) -> dict:
    best = results[0]
    q    = query.strip().lower()
    t    = best["title"].strip().lower()
    confident = q == t or q in t or t in q or best["score"] >= SHOW_SIM
    if confident:
        return build_alert_card(query, results)
    rel = [r for r in results if r["score"] >= MIN_SIM]
    if not rel:
        return build_none_card(query)
    return build_list_card(query, rel)


# send
async def send_lark_message(receive_id: str, text: str, receive_id_type: str = "chat_id"):
    await _send(receive_id, "text", json.dumps({"text": text}), receive_id_type)


async def send_lark_card(receive_id: str, card: dict, receive_id_type: str = "chat_id"):
    await _send(receive_id, "interactive", json.dumps(card), receive_id_type)


async def _send(receive_id: str, msg_type: str, content: str, receive_id_type: str):
    token = await get_lark_token()
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{LARK_API_BASE}/im/v1/messages?receive_id_type={receive_id_type}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": receive_id,
                "msg_type":   msg_type,
                "content":    content,
            },
        )
        data = {}
        try:
            data = r.json()
        except Exception:
            pass
        if r.status_code != 200 or data.get("code", 0) != 0:
            logger.error(f"send failed ({msg_type}): {r.status_code} {r.text}")
        r.raise_for_status()


# health
@app.get("/")
async def health_check():
    return {"status": "healthy", "sources": sources_loaded, "alerts": len(alerts)}


# chat endpoint
@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    return ChatResponse(query=request.text, results=search_alerts(request.text, request.top_k))


# dedupe retried webhook deliveries (Lark retries if we don't ack fast enough)
_seen_event_ids: set = set()


async def _handle_message(raw: str, chat_id: str):
    text = clean_text(raw)
    receive_id, receive_id_type = chat_id, "chat_id"

    logger.info(f"incoming text={text!r} chat_id={chat_id}")

    low = text.lower()
    if low == "/s" or low.startswith("/s "):
        # search -> card
        query = text[2:].strip()
        if not query:
            await send_lark_message(receive_id, "What should I search for? e.g. /s pod restart", receive_id_type)
        elif not alerts:
            await send_lark_message(receive_id, "No alerts loaded yet.", receive_id_type)
        else:
            results = search_alerts(query, top_k=5)
            card    = build_search_card(query, results)
            await send_lark_card(receive_id, card, receive_id_type)
        return

    # ping
    if low == "ping":
        ms = await measure_lark_ping()
        await send_lark_message(receive_id, f"pong {ms:.0f}ms", receive_id_type)
        return

    # chat
    await send_lark_message(receive_id, chat_reply(text), receive_id_type)


# webhook
@app.post("/lark/webhook")
async def lark_webhook(request: Request):
    body = await request.json()

    # verification
    if "challenge" in body:
        return {"challenge": body["challenge"]}

    # dedupe: Lark retries the same event_id if we don't ack in time
    event_id = body.get("header", {}).get("event_id")
    if event_id:
        if event_id in _seen_event_ids:
            return {"status": "duplicate"}
        _seen_event_ids.add(event_id)
        if len(_seen_event_ids) > 1000:
            _seen_event_ids.clear()

    event = body.get("event", {})
    msg   = event.get("message", {})

    if msg.get("message_type") != "text":
        return {"status": "ignored"}

    # message
    try:
        raw = json.loads(msg["content"]).get("text", "")
    except (KeyError, json.JSONDecodeError):
        return {"status": "bad_content"}

    chat_id = msg.get("chat_id")
    if not chat_id:
        return {"status": "no_chat_id"}

    # ack immediately; process + reply in the background so Lark doesn't
    # time out and redeliver the same event
    asyncio.create_task(_handle_message(raw, chat_id))
    return {"status": "ok"}