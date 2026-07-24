import re

from config import (
    MIN_SIM,
    SHOW_SIM
)


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


# clean up action steps
def clean_action(title: str, action: str) -> str:

    if not action or not action.strip():
        return action

    out = []

    for line in action.splitlines():

        line = line.strip()

        if not line:
            continue

        # remove existing bullets/numbers
        line = re.sub(
            r"^(\d+[.)]\s*|[-*•]\s*)",
            "",
            line
        )

        if not line:
            continue

        out.append(
            f"- {line}"
        )

    return "\n".join(out) if out else action



# full alert detail card
def build_alert_card(
        query: str,
        results: list
) -> dict:

    best = results[0]


    doc = (
        f"[{best['doc']}]({best['url']})"
        if best["url"]
        else best["doc"]
    )


    head = "\n".join([
        f"**Query:** {query}",
        f"**Doc:** {doc}",
        f"**Alert:** {best['title']}",
        f"**Severity:** {best['severity'] or '-'}",
        f"**Who to tag:** {best['tag'] or '-'}",
    ])


    action = clean_action(
        best["title"],
        best["action"] or ""
    )


    action = (action or "-")[:800]


    elements = [

        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": head
            }
        },

        {
            "tag": "hr"
        },

        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content":
                f"**Action to do:**\n{action}"
            }
        }

    ]


    # show other possible alerts
    if len(results) > 1:

        others = [
            r for r in results[1:]
            if r["score"] >= MIN_SIM
        ]


        if others:

            lines = "\n".join(
                f"- {r['title']} ({r['score']:.0%})"
                for r in others
            )


            elements.append(
                {
                    "tag":"hr"
                }
            )


            elements.append(
                {
                    "tag":"div",
                    "text":{
                        "tag":"lark_md",
                        "content":
                        f"**Other matches:**\n{lines}"
                    }
                }
            )


    return {

        "config":{
            "wide_screen_mode":True
        },

        "header":{

            "title":{
                "tag":"plain_text",
                "content":best["title"]
            },

            "template":
            sev_color(
                best["severity"]
            )
        },

        "elements":elements
    }




# list possible matches card
def build_list_card(
        query:str,
        results:list
) -> dict:


    lines="\n".join(
        f"- {r['title']} ({r['score']:.0%})"
        for r in results
    )


    body = (
        f"**Query:** {query}\n"
        "No exact alert match. Closest titles:"
    )


    hint = (
        "Search the full title for details, e.g.\n/s "
        + results[0]["title"]
    )


    return {

        "config":{
            "wide_screen_mode":True
        },


        "header":{

            "title":{
                "tag":"plain_text",
                "content":"Possible matches"
            },

            "template":"blue"
        },


        "elements":[

            {
                "tag":"div",
                "text":{
                    "tag":"lark_md",
                    "content":body
                }
            },

            {
                "tag":"hr"
            },

            {
                "tag":"div",
                "text":{
                    "tag":"lark_md",
                    "content":lines
                }
            },


            {
                "tag":"div",
                "text":{
                    "tag":"lark_md",
                    "content":hint
                }
            }

        ]
    }




# no result card
def build_none_card(query:str) -> dict:


    return {

        "config":{
            "wide_screen_mode":True
        },


        "header":{

            "title":{
                "tag":"plain_text",
                "content":"No match"
            },

            "template":"grey"

        },


        "elements":[

            {
                "tag":"div",
                "text":{
                    "tag":"lark_md",
                    "content":
                    f"**Query:** {query}\n"
                    "No matching alert found."
                }
            }

        ]
    }

# decide which card to show
def build_search_card(
        query:str,
        results:list
) -> dict:

    if not results:
        return build_none_card(query)
    
    best = results[0]
    q = query.strip().lower()
    t = best["title"].strip().lower()

    confident = (
        q == t
        or q in t
        or t in q
        or best["score"] >= SHOW_SIM
    )

    if confident:

        return build_alert_card(
            query,
            results
        )

    rel = [
        r for r in results
        if r["score"] >= MIN_SIM
    ]

    if not rel:

        return build_none_card(query)

    return build_list_card(
        query,
        rel
    )