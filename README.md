# RingCentral Voicemail -> Discord (Polling)

This script polls RingCentral for **Unread voicemails** in a specific extension (default: extension `1`) and posts each voicemail to a Discord channel via webhook, then marks the voicemail as **Read** so it does not repost.

## What it posts
- Caller ID name
- Phone number
- Time received
- Transcription (if available)

Discord embed field values are capped at 1024 characters, so the script trims long transcriptions automatically.  
(Useful limit reference: 1024 chars for embed field values.)  

## Prereqs
- Python 3.10+ recommended
- A RingCentral app using JWT auth
- RingCentral app permission: **Read Messages** (required to read Message Store / voicemails)
- A Discord webhook URL for the destination channel

## Setup
1. **Create a RingCentral JWT app**
   - In RingCentral Developer Console, create an app that supports **JWT auth**.
   - Copy:
     - Client ID
     - Client Secret
     - JWT credential (this is not an access token, treat it like a password)

2. **Create a Discord webhook**
   - In Discord channel settings, create a webhook.
   - Copy the webhook URL.

3. **Create your `.env`**
   - Copy `.env.example` to `.env`
   - Fill in the real values:
     - `RC_CLIENT_ID`
     - `RC_CLIENT_SECRET`
     - `RC_JWT`
     - `DISCORD_WEBHOOK_URL`

4. **Install dependencies**
   ```bash
   pip install flask requests python-dotenv
