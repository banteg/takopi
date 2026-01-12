# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "mistune>=3.2.0",
#     "requests>=2.32.5",
#     "sulguk>=0.11.1",
# ]
# ///
import mistune
import sulguk
import requests
import os
import re

repo = os.environ["REPO"]
tag = os.environ["TAG_NAME"]
bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]

PULL_RE = re.compile(rf"(https://github.com/{repo}/pull/(\d+))")
COMPARE_RE = re.compile(rf"(https://github.com/{repo}/compare/(.*))")

resp = requests.get(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
body = resp.json()["body"]
lines = body.splitlines()

header = f"release **{repo} {tag}**"
pulls = "\n".join(
    [PULL_RE.sub(r"[#\2](\1)", line) for line in lines if PULL_RE.search(line)]
)
compare = COMPARE_RE.sub(r"compare [\2](\1)", COMPARE_RE.search(body).group(0))

message = "\n\n".join([header, pulls, compare])
print(message)

html = mistune.html(message)
rendered = sulguk.transform_html(html)

payload = {
    "chat_id": chat_id,
    "text": rendered.text,
    "entities": rendered.entities,
    "link_preview_options": {"is_disabled": True},
}
resp = requests.post(
    f"https://api.telegram.org/bot{bot_token}/sendMessage", json=payload
)
resp.raise_for_status()
print(f"\nsent to {chat_id}")
