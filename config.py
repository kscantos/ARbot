import os
from dotenv import load_dotenv

load_dotenv()

LARK_APP_ID = os.getenv("LARK_APP_ID", "")
LARK_APP_SECRET = os.getenv("LARK_APP_SECRET", "")

LARK_API_BASE = "https://open.larksuite.com/open-apis"
LARK_HOST = "https://casinoplus.sg.larksuite.com"

# Lark Wiki node tokens to sync alert docs from, comma-separated.
WIKI_NODE_TOKENS = [
    t for t in os.getenv("WIKI_NODE_TOKENS", "").split(",") if t
]

SHOW_SIM = 0.45
MIN_SIM = 0.35