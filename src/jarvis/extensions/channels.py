"""Official outbound channels with explicit owner destinations and no webhook server."""

import re
from xml.sax.saxutils import escape

import httpx

from .common import ExtensionError, Journal, PolicyDenied, availability, bounded_text, credential, post


def phone(value):
    if not isinstance(value, str) or not re.fullmatch(r'\+[1-9][0-9]{7,14}', value):
        raise ExtensionError('Owner phone number must use E.164 format')
    return value


class Telegram:
    def __init__(self, *, owner_chat_id, owner_user_id=None, transport=None):
        self.owner_chat_id = str(owner_chat_id)
        self.owner_user_id = str(owner_user_id or owner_chat_id)
        if not re.fullmatch(r'-?[0-9]{1,20}', self.owner_chat_id) or not self.owner_user_id.isdigit():
            raise ExtensionError('Telegram requires configured owner chat and user identifiers')
        self.transport = transport

    def available(self):
        return availability('TELEGRAM_BOT_TOKEN')

    def check_owner(self, update):
        message = update.get('message', {}) if isinstance(update, dict) else {}
        chat = message.get('chat', {}) if isinstance(message, dict) else {}
        sender = message.get('from', {}) if isinstance(message, dict) else {}
        if (not isinstance(chat, dict) or not isinstance(sender, dict)
                or str(chat.get('id')) != self.owner_chat_id
                or str(sender.get('id')) != self.owner_user_id):
            raise ExtensionError('Telegram sender is not the configured owner')
        return self.owner_user_id

    def send(self, text):
        token = credential('TELEGRAM_BOT_TOKEN')
        if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
            raise ExtensionError('Telegram bot credential format is invalid')
        result = post(f'https://api.telegram.org/bot{token}/sendMessage', transport=self.transport,
                      json_body={'chat_id': self.owner_chat_id, 'text': bounded_text(text),
                                 'link_preview_options': {'is_disabled': True}})
        receipt = result.get('result', {})
        chat = receipt.get('chat', {}) if isinstance(receipt, dict) else {}
        if (not result.get('ok') or not isinstance(receipt, dict) or not isinstance(chat, dict)
                or str(chat.get('id')) != self.owner_chat_id):
            raise ExtensionError('Telegram did not confirm the configured destination')
        if not isinstance(receipt.get('message_id'), int):
            raise ExtensionError('Telegram did not return a message receipt')
        return {'service': 'telegram', 'external_id': str(receipt['message_id']), 'status': 'accepted'}


class WhatsApp:
    def __init__(self, *, phone_number_id, owner_number, transport=None):
        self.phone_number_id = str(phone_number_id)
        if not re.fullmatch(r'[0-9]{1,24}', self.phone_number_id):
            raise ExtensionError('WhatsApp phone identifier must be numeric')
        self.owner_number = phone(owner_number)
        self.transport = transport

    def available(self):
        return availability('WHATSAPP_ACCESS_TOKEN')

    def send(self, text):
        result = post(f'https://graph.facebook.com/v23.0/{self.phone_number_id}/messages',
                      transport=self.transport,
                      headers={'Authorization': 'Bearer ' + credential('WHATSAPP_ACCESS_TOKEN')},
                      json_body={'messaging_product': 'whatsapp', 'to': self.owner_number[1:],
                                 'type': 'text', 'text': {'body': bounded_text(text), 'preview_url': False}})
        messages = result.get('messages', [])
        if (not isinstance(messages, list) or not messages or not isinstance(messages[0], dict)
                or not isinstance(messages[0].get('id'), str) or not messages[0]['id']):
            raise ExtensionError('WhatsApp did not return a message receipt')
        return {'service': 'whatsapp', 'external_id': str(messages[0]['id']), 'status': 'accepted'}


class Twilio:
    def __init__(self, *, owner_number, from_number, journal, daily_call_cap=2,
                 daily_seconds_cap=120, max_call_seconds=60, daily_message_cap=5, transport=None):
        self.owner_number, self.from_number = phone(owner_number), phone(from_number)
        if (any(not isinstance(v, int) or isinstance(v, bool) for v in
                (daily_call_cap, max_call_seconds, daily_seconds_cap, daily_message_cap))
                or not (0 <= daily_call_cap <= 20 and 1 <= max_call_seconds <= 120
                and 0 <= daily_seconds_cap <= 2400 and 0 <= daily_message_cap <= 100)):
            raise ExtensionError('Twilio budgets exceed the supported limits')
        self.daily_call_cap, self.max_call_seconds = daily_call_cap, max_call_seconds
        self.daily_seconds_cap, self.daily_message_cap = daily_seconds_cap, daily_message_cap
        self.journal, self.transport = Journal(journal), transport

    def available(self):
        return availability('TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN')

    def _send(self, resource, payload):
        sid = credential('TWILIO_ACCOUNT_SID')
        if not re.fullmatch(r'AC[0-9a-fA-F]{32}', sid):
            raise ExtensionError('Twilio account identifier format is invalid')
        result = post(f'https://api.twilio.com/2010-04-01/Accounts/{sid}/{resource}.json',
                      transport=self.transport, data=payload,
                      auth=httpx.BasicAuth(sid, credential('TWILIO_AUTH_TOKEN')))
        if not isinstance(result.get('sid'), str) or not result['sid']:
            raise ExtensionError('Twilio did not return a receipt')
        return {'service': 'twilio', 'external_id': result['sid'], 'status': 'accepted',
                'provider_status': result.get('status', 'unknown')}

    def call(self, text, *, key):
        self._require_credentials()
        text = bounded_text(text, 1000)
        payload = {'To': self.owner_number, 'From': self.from_number,
                   'Twiml': '<Response><Say>' + escape(text) + '</Say><Hangup/></Response>',
                   'TimeLimit': str(self.max_call_seconds), 'Timeout': '20'}
        scope = 'twilio-call:' + self.owner_number
        old = self.journal.reserve(scope, key, payload, self.daily_call_cap,
                                   units=self.max_call_seconds, unit_cap=self.daily_seconds_cap)
        if old:
            return old
        return self.journal.complete(scope, key, self._send('Calls', payload))

    def _require_credentials(self):
        credential('TWILIO_ACCOUNT_SID')
        credential('TWILIO_AUTH_TOKEN')

    def message(self, text, *, key):
        self._require_credentials()
        payload = {'To': self.owner_number, 'From': self.from_number, 'Body': bounded_text(text, 1600)}
        scope = 'twilio-message:' + self.owner_number
        old = self.journal.reserve(scope, key, payload, self.daily_message_cap)
        if old:
            return old
        return self.journal.complete(scope, key, self._send('Messages', payload))

    def notify(self, text, *, key, message_fallback=True):
        try:
            return self.call(text, key=key)
        except PolicyDenied:
            raise
        except ExtensionError:
            if not message_fallback:
                raise
            receipt = self.message(text, key=key + ':fallback')
            return {**receipt, 'fallback': True, 'call_status': 'failed_or_uncertain'}
