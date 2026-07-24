import random

GREETING_WORDS = {
    "hello",
    "hi",
    "hey"
}

GREETING_REPLIES = [
    "hello!",
    "hey there",
    "yo"
]

def clean_text(raw):

    text = raw.strip()

    return " ".join(
        text.split()
    )

def chat_reply(text):

    low=text.lower()

    if low in GREETING_WORDS:
        return random.choice(
            GREETING_REPLIES
        )

    return "???"