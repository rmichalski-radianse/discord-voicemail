import base64
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

# Optional: load .env for local dev (do not commit .env to git)
try:
    from dotenv import load_dotenv  # pip install python-dotenv
    load_dotenv()
except Exception:
    pass


def must_get_env(key: str) -> str:
    """Require an environment variable. Exit with a clean message if missing."""
    val = os.getenv(key)
    if not val:
        raise SystemExit(f"Missing required environment variable: {key}")
    return val


# RingCentral config
RC_SERVER = os.getenv("RC_SERVER", "https://platform.ringcentral.com")
RC_CLIENT_ID = must_get_env("RC_CLIENT_ID")
RC_CLIENT_SECRET = must_get_env("RC_CLIENT_SECRET")
RC_JWT = must_get_env("RC_JWT")
RC_ACCOUNT_ID = os.getenv("RC_ACCOUNT_ID", "~")
RC_EXTENSION_ID = must_get_env("RC_EXTENSION_ID")  # Example: "1" for Radianse Reception

# Discord config
DISCORD_WEBHOOK_URL = must_get_env("DISCORD_WEBHOOK_URL")

# Polling config
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
PER_PAGE = int(os.getenv("PER_PAGE", "50"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "10"))  # safety cap

# Token cache
_token: Optional[str] = None
_token_exp: float = 0.0


def get_access_token() -> str:
    """
    JWT OAuth flow to get an access token.
    Re-use token until close to expiry to avoid auth rate limits.
    """
    global _token, _token_exp
    now = time.time()
    if _token and now < (_token_exp - 60):
        return _token

    token_url = f"{RC_SERVER}/restapi/oauth/token"
    basic = base64.b64encode(f"{RC_CLIENT_ID}:{RC_CLIENT_SECRET}".encode()).decode()

    r = requests.post(
        token_url,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": RC_JWT,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    _token = data["access_token"]
    _token_exp = now + int(data.get("expires_in", 3600))
    return _token


def rc_headers() -> dict:
    return {"Authorization": f"Bearer {get_access_token()}", "Accept": "application/json"}


def rc_get(path: str, params=None) -> dict:
    r = requests.get(f"{RC_SERVER}{path}", headers=rc_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def rc_patch(path: str, body: dict) -> None:
    # RingCentral supports PATCH to update message readStatus. :contentReference[oaicite:1]{index=1}
    r = requests.patch(f"{RC_SERVER}{path}", headers=rc_headers(), json=body, timeout=30)
    r.raise_for_status()


def rc_get_text(uri: str) -> str:
    # Attachment URI can be absolute or relative; handle both.
    url = uri if uri.startswith("http") else f"{RC_SERVER}{uri}"
    r = requests.get(url, headers={"Authorization": f"Bearer {get_access_token()}"}, timeout=30)
    r.raise_for_status()
    return r.text


def discord_post(caller_name: str, caller_number: str, creation_time: str, transcription: Optional[str]) -> None:
    transcription_text = (transcription or "(No transcription available yet.)").strip()
    transcription_text = transcription_text[:1024]  # Discord embed field value limit

    payload = {
        "content": "New voicemail",
        "embeds": [
            {
                "title": "Voicemail received",
                "fields": [
                    {"name": "Extension", "value": str(RC_EXTENSION_ID), "inline": True},
                    {"name": "Caller", "value": caller_name or "(unknown)", "inline": True},
                    {"name": "Number", "value": caller_number or "(unknown)", "inline": True},
                    {"name": "Time", "value": creation_time or "(unknown)", "inline": False},
                    {"name": "Transcription", "value": transcription_text, "inline": False},
                ],
            }
        ],
    }
    r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
    r.raise_for_status()


def fetch_message(message_id: str) -> dict:
    return rc_get(f"/restapi/v1.0/account/{RC_ACCOUNT_ID}/extension/{RC_EXTENSION_ID}/message-store/{message_id}")


def fetch_transcription_with_retry(message_id: str, retries: int = 6, sleep_seconds: float = 2.0) -> Optional[str]:
    """
    Transcription often arrives slightly after the voicemail record.
    Retry a few times to catch the AudioTranscription attachment if it appears.
    """
    for _ in range(retries):
        msg = fetch_message(message_id)
        for att in (msg.get("attachments") or []):
            if att.get("type") == "AudioTranscription" and att.get("uri"):
                return rc_get_text(att["uri"]).strip()
        time.sleep(sleep_seconds)
    return None


def mark_as_read(message_id: str) -> None:
    # Only readStatus can be updated in this context. :contentReference[oaicite:2]{index=2}
    rc_patch(
        f"/restapi/v1.0/account/{RC_ACCOUNT_ID}/extension/{RC_EXTENSION_ID}/message-store/{message_id}",
        {"readStatus": "Read"},
    )


def list_unread_voicemails(date_from_iso: str) -> list[dict]:
    """
    Walk pages of message-store results to avoid missing voicemails.
    listMessages supports messageType and readStatus filters. :contentReference[oaicite:3]{index=3}
    """
    all_records: list[dict] = []
    page = 1

    while page <= MAX_PAGES:
        data = rc_get(
            f"/restapi/v1.0/account/{RC_ACCOUNT_ID}/extension/{RC_EXTENSION_ID}/message-store",
            params={
                "messageType": "VoiceMail",
                "readStatus": "Unread",
                "dateFrom": date_from_iso,
                "perPage": PER_PAGE,
                "page": page,
            },
        )

        records = data.get("records") or []
        all_records.extend(records)

        paging = data.get("paging") or {}
        total_pages = int(paging.get("totalPages") or 1)
        if page >= total_pages:
            break
        page += 1

    return all_records


def main() -> None:
    # Keep result set bounded, but unread messages older than this window might be skipped.
    # If you want "never miss any unread", increase DAYS_BACK or remove it.
    days_back = int(os.getenv("DAYS_BACK", "14"))
    date_from = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()

    print(f"Starting: polling every {POLL_SECONDS}s for unread voicemails on extension {RC_EXTENSION_ID}")

    while True:
        try:
            records = list_unread_voicemails(date_from)

            # Oldest first so Discord posts in the order they were left
            records.sort(key=lambda x: x.get("creationTime", ""))

            for msg in records:
                msg_id = str(msg["id"])

                # Fetch full message details (sometimes list records are not complete)
                full = fetch_message(msg_id)
                from_info = full.get("from") or {}

                caller_name = from_info.get("name") or ""
                caller_number = from_info.get("phoneNumber") or ""
                creation_time = full.get("creationTime") or msg.get("creationTime") or ""

                transcription = fetch_transcription_with_retry(msg_id)

                discord_post(caller_name, caller_number, creation_time, transcription)
                mark_as_read(msg_id)

        except Exception as e:
            print(f"[error] {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
