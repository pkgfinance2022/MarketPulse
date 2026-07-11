"""
Telegram notifier.

Sends a message via a Telegram bot so alerts reach the user's phone
even when the browser tab isn't open (unlike the desktop notification,
which only fires while the tab is alive). Credentials are read from
Streamlit secrets (.streamlit/secrets.toml, already gitignored) - never
hardcoded here, so the token can't end up committed to source control.
"""

import requests
import streamlit as st

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:

    @staticmethod
    def _credentials():

        token = st.secrets.get("TELEGRAM_BOT_TOKEN")
        chat_id = st.secrets.get("TELEGRAM_CHAT_ID")

        return token, chat_id

    @classmethod
    def is_configured(cls):

        token, chat_id = cls._credentials()

        return bool(token and chat_id)

    @classmethod
    def send(cls, text):
        """
        Best-effort send - a Telegram outage or bad credentials
        shouldn't ever crash the dashboard, so failures are swallowed
        after one attempt.
        """

        token, chat_id = cls._credentials()

        if not token or not chat_id:
            return False

        try:
            response = requests.post(
                TELEGRAM_API.format(token=token),
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
            return response.ok
        except requests.RequestException:
            return False
