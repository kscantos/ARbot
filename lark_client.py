import httpx

from config import (
    LARK_APP_ID,
    LARK_APP_SECRET,
    LARK_API_BASE
)

async def get_lark_token():

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{LARK_API_BASE}/auth/v3/tenant_access_token/internal",
            json={
                "app_id": LARK_APP_ID,
                "app_secret": LARK_APP_SECRET
            }
        )

        r.raise_for_status()

        return r.json()["tenant_access_token"]

async def fetch_wiki_obj_token(node_token, token):

    async with httpx.AsyncClient() as client:

        r = await client.get(
            f"{LARK_API_BASE}/wiki/v2/spaces/get_node",
            params={
                "token": node_token
            },
            headers={
                "Authorization": f"Bearer {token}"
            }
        )

        r.raise_for_status()

        return (
            r.json()
            .get("data", {})
            .get("node", {})
            .get("obj_token", "")
        )


async def fetch_doc_blocks(document_id, token):

    blocks = []
    page_token = None

    async with httpx.AsyncClient() as client:

        while True:

            params = {"page_size": 500}

            if page_token:
                params["page_token"] = page_token

            r = await client.get(
                f"{LARK_API_BASE}/docx/v1/documents/{document_id}/blocks",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}"
                }
            )

            r.raise_for_status()

            data = r.json()["data"]

            blocks.extend(data.get("items", []))

            if not data.get("has_more"):
                break

            page_token = data.get("page_token")

    return blocks



async def send_lark_message(
        receive_id,
        text,
        receive_id_type="chat_id"
):

    await _send(
        receive_id,
        "text",
        '{"text":"' + text + '"}',
        receive_id_type
    )

async def send_lark_card(
        receive_id,
        card,
        receive_id_type="chat_id"
):

    import json

    await _send(
        receive_id,
        "interactive",
        json.dumps(card),
        receive_id_type
    )

async def _send(
        receive_id,
        msg_type,
        content,
        receive_id_type
):

    token = await get_lark_token()

    async with httpx.AsyncClient() as client:

        r = await client.post(
            f"{LARK_API_BASE}/im/v1/messages?receive_id_type={receive_id_type}",
            headers={
                "Authorization": f"Bearer {token}"
            },
            json={
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": content
            }
        )

        r.raise_for_status()