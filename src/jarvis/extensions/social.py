"""Bluesky official text publishing under a persistent owner standing policy."""

from datetime import UTC, datetime

from .common import ExtensionError, Journal, availability, bounded_text, credential, post


class SocialPublisher:
    def __init__(self, journal, *, daily_cap=3, forbidden_terms=(), transport=None):
        if not isinstance(daily_cap, int) or not 0 <= daily_cap <= 100:
            raise ExtensionError('Publishing daily cap must be between 0 and 100')
        if len(forbidden_terms) > 100 or any(not isinstance(term, str) or not term.strip()
                                             or len(term) > 200 for term in forbidden_terms):
            raise ExtensionError('Content policy must contain bounded, nonempty terms')
        self.daily_cap, self.forbidden_terms = daily_cap, tuple(t.casefold() for t in forbidden_terms)
        self.journal, self.transport = Journal(journal), transport

    def available(self):
        return availability('BLUESKY_HANDLE', 'BLUESKY_APP_PASSWORD')

    def _scope(self):
        return 'bluesky:' + credential('BLUESKY_HANDLE').casefold()

    def set_paused(self, paused):
        if not isinstance(paused, bool):
            raise ExtensionError('Pause state must be a boolean')
        self.journal.paused(self._scope(), paused)

    def publish(self, text, *, key):
        text = bounded_text(text, 300)
        if any(term in text.casefold() for term in self.forbidden_terms):
            raise ExtensionError('Content is prohibited by the owner publishing policy')
        handle, password = credential('BLUESKY_HANDLE'), credential('BLUESKY_APP_PASSWORD')
        scope = self._scope()
        old = self.journal.reserve(scope, key, {'text': text}, self.daily_cap)
        if old:
            return old
        session = post('https://bsky.social/xrpc/com.atproto.server.createSession',
                       transport=self.transport, json_body={'identifier': handle, 'password': password})
        token, did = session.get('accessJwt'), session.get('did')
        if not isinstance(token, str) or not isinstance(did, str) or not did.startswith('did:'):
            raise ExtensionError('Bluesky did not return a valid session')
        result = post('https://bsky.social/xrpc/com.atproto.repo.createRecord', transport=self.transport,
                      headers={'Authorization': 'Bearer ' + token},
                      json_body={'repo': did, 'collection': 'app.bsky.feed.post',
                                 'record': {'$type': 'app.bsky.feed.post', 'text': text,
                                            'createdAt': datetime.now(UTC).isoformat()}})
        uri = result.get('uri')
        if not isinstance(uri, str) or not uri.startswith('at://'):
            raise ExtensionError('Bluesky did not return a publication receipt')
        return self.journal.complete(scope, key, {'service': 'bluesky', 'external_id': uri,
                                                'status': 'accepted'})
