import re
import numpy as np
from sentence_transformers import SentenceTransformer

from config import (
    WIKI_NODE_TOKENS,
    LARK_HOST
)

from lark_client import (
    get_lark_token,
    fetch_wiki_obj_token,
    fetch_doc_blocks
)

model = SentenceTransformer(
    "all-MiniLM-L6-v2"
)

alerts = []
alert_titles = []
alert_embeddings = []

# docx heading block_type codes (heading1..heading9)
HEADING_BLOCK_TYPES = {3, 4, 5, 6, 7, 8, 9, 10, 11}

def _block_text(block):

    for key in (
        "text",
        "heading1", "heading2", "heading3",
        "heading4", "heading5", "heading6",
        "heading7", "heading8", "heading9"
    ):

        node = block.get(key)

        if node:
            return "".join(
                el.get("text_run", {}).get("content", "")
                for el in node.get("elements", [])
            )

    return ""

def parse_doc_blocks(blocks):

    sections = []
    title = None
    body_lines = []

    for block in blocks:

        text = _block_text(block)

        if block.get("block_type") in HEADING_BLOCK_TYPES:

            if title is not None:
                sections.append({
                    "title": title.strip(),
                    "body": "\n".join(body_lines).strip()
                })

            title = text
            body_lines = []

        elif title is not None and text:
            body_lines.append(text)

    if title is not None:
        sections.append({
            "title": title.strip(),
            "body": "\n".join(body_lines).strip()
        })

    return [s for s in sections if s["title"]]

async def sync_wiki_docs():

    if not WIKI_NODE_TOKENS:
        return

    token = await get_lark_token()

    for node_token in WIKI_NODE_TOKENS:

        obj_token = await fetch_wiki_obj_token(node_token, token)

        if not obj_token:
            continue

        blocks = await fetch_doc_blocks(obj_token, token)
        sections = parse_doc_blocks(blocks)

        register_sections(
            node_token,
            f"{LARK_HOST}/wiki/{node_token}",
            sections
        )

    build_embeddings()

def register_sections(
        doc_name,
        doc_url,
        sections
):

    for s in sections:

        alerts.append({
            "doc": doc_name,
            "url": doc_url,
            "title": s["title"],
            "severity": "",
            "tag": "",
            "action": s["body"]
        })

        alert_titles.append(
            s["title"]
        )



def build_embeddings():

    global alert_embeddings

    if alert_titles:

        alert_embeddings = model.encode(
            alert_titles,
            normalize_embeddings=True
        )

def search_alerts(query, top_k=5):

    if len(alert_embeddings)==0:
        return []

    q = model.encode(
        [query],
        normalize_embeddings=True
    )

    sims = np.dot(
        q,
        alert_embeddings.T
    )[0]


    idx = np.argsort(sims)[-top_k:][::-1]

    return [
        {
            **alerts[i],
            "score":float(sims[i])
        }
        for i in idx
    ]