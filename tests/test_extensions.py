
import httpx
import pytest

from jarvis.extensions.business import score_returns, score_returns_range, summarize_csv
from jarvis.extensions.channels import ExtensionError, Telegram, Twilio, WhatsApp
from jarvis.extensions.common import post
from jarvis.extensions.creative import ImageGenerator, video_command, write_website
from jarvis.extensions.social import SocialPublisher
from jarvis.extensions.voice import LocalVoice


def test_business_decimal_and_invalid_inputs(tmp_path):
    path = tmp_path / 'sales.csv'
    path.write_text('revenue,cost\n0.10,0.03\n0.20,0.07\n')
    result = summarize_csv(path)
    assert result['revenue'] == '0.30'
    assert result['profit'] == '0.20'
    assert score_returns('0.5', '100', '10', '5')['net_value'] == '35.0'
    path.write_text('revenue,cost\nNaN,0\n')
    with pytest.raises(ExtensionError):
        summarize_csv(path)
    with pytest.raises(ExtensionError):
        score_returns('1.1', '100', '0', '0')
    assert score_returns_range(('0.4', '0.6'), ('100', '120'), ('10', '20'), ('0', '5')) == {
        'min_net_value': '15.0', 'max_net_value': '62.0', 'currency': 'caller_supplied_same_unit'}
    with pytest.raises(ExtensionError):
        score_returns('1', '100', '0', '0', privacy_allowed='yes')


def test_website_escapes_text_and_external_opt_in(tmp_path):
    path = tmp_path / 'site.html'
    write_website(path, title='<script>bad()</script>', description='Shop', metrics={'profit': '20'})
    html = path.read_text()
    assert '&lt;script&gt;' in html
    assert 'https://' not in html
    write_website(path, title='Shop', description='Business', metrics={}, allow_cdn=True)
    assert 'three@' in path.read_text()
    assert 'External CDN' in path.read_text()


def test_video_bounded_args_and_missing_optional_engines(tmp_path):
    args = video_command(tmp_path / 'input.mp4', tmp_path / 'out.mp4', start=0, duration=2)
    assert args[0] == 'ffmpeg'
    assert '-nostdin' in args and '-threads' in args
    with pytest.raises(ExtensionError):
        video_command(tmp_path / 'input.mp4', tmp_path / 'out.mp4', start=-1, duration=2)
    assert not ImageGenerator().available()['available']
    assert not LocalVoice().available()['available']


def test_telegram_owner_credential_and_contract(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True, 'result': {'message_id': 23, 'chat': {'id': 123}}})
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '123:secret')
    adapter = Telegram(owner_chat_id='123', transport=httpx.MockTransport(handler))
    assert adapter.send('Hello')['external_id'] == '23'
    assert requests[0].url.host == 'api.telegram.org'
    with pytest.raises(ExtensionError):
        adapter.check_owner({'message': {'chat': {'id': 124}, 'from': {'id': 123}}})
    assert adapter.check_owner({'message': {'chat': {'id': 123}, 'from': {'id': 123}}, 'update_id': 7}) == '123'
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN')
    assert not adapter.available()['available']


def test_whatsapp_and_twilio_budget(monkeypatch, tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.host == 'graph.facebook.com':
            return httpx.Response(200, json={'messages': [{'id': 'wa-id'}]})
        return httpx.Response(201, json={'sid': 'CA123', 'status': 'queued'})
    for name in ['WHATSAPP_ACCESS_TOKEN', 'TWILIO_AUTH_TOKEN']:
        monkeypatch.setenv(name, 'secret')
    monkeypatch.setenv('TWILIO_ACCOUNT_SID', 'AC' + 'a' * 32)
    wa = WhatsApp(phone_number_id='456', owner_number='+15551234567', transport=httpx.MockTransport(handler))
    assert wa.send('Hello')['external_id'] == 'wa-id'
    twilio = Twilio(owner_number='+15551234567', from_number='+15557654321',
                    journal=tmp_path / 'calls.db', daily_call_cap=1, transport=httpx.MockTransport(handler))
    assert twilio.call('Reminder', key='one')['external_id'] == 'CA123'
    assert twilio.call('Reminder', key='one')['external_id'] == 'CA123'
    with pytest.raises(ExtensionError):
        twilio.call('Reminder', key='two')
    assert len(requests) == 2
    assert b'%2B15551234567' in requests[1].content
    with pytest.raises(ExtensionError):
        twilio.notify('Different reminder', key='one')
    assert len(requests) == 2


def test_social_policy_idempotency_and_failure_ambiguity(monkeypatch, tmp_path):
    monkeypatch.setenv('BLUESKY_HANDLE', 'owner.bsky.social')
    monkeypatch.setenv('BLUESKY_APP_PASSWORD', 'secret')
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('createSession'):
            return httpx.Response(200, json={'accessJwt': 'jwt', 'did': 'did:plc:owner'})
        return httpx.Response(200, json={'uri': 'at://owner/post/one', 'cid': 'cid'})
    adapter = SocialPublisher(tmp_path / 'social.db', daily_cap=1,
                              transport=httpx.MockTransport(handler))
    assert adapter.publish('Useful update', key='one')['external_id'].startswith('at://')
    assert adapter.publish('Useful update', key='one')['external_id'].startswith('at://')
    assert len(calls) == 2
    with pytest.raises(ExtensionError):
        adapter.publish('Different text', key='one')
    with pytest.raises(ExtensionError):
        adapter.publish('Second update', key='two')
    adapter.set_paused(True)
    with pytest.raises(ExtensionError):
        adapter.publish('Paused', key='three')


def test_external_errors_redacted_and_redirects_rejected(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '123:secret')
    adapter = Telegram(owner_chat_id='123', transport=httpx.MockTransport(
        lambda req: httpx.Response(302, headers={'location': 'http://127.0.0.1/secret'})))
    with pytest.raises(ExtensionError) as error:
        adapter.send('Hi')
    assert 'secret' not in str(error.value)


def test_malformed_telegram_receipt_and_sender_are_safe(monkeypatch):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '123:secret')
    adapter = Telegram(owner_chat_id='123', transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={'ok': True, 'result': []})))
    with pytest.raises(ExtensionError):
        adapter.send('Hi')
    with pytest.raises(ExtensionError):
        adapter.check_owner({'message': {'chat': None, 'from': []}})


def test_social_uncertain_send_not_repeated(monkeypatch, tmp_path):
    monkeypatch.setenv('BLUESKY_HANDLE', 'owner.bsky.social')
    monkeypatch.setenv('BLUESKY_APP_PASSWORD', 'secret')
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('secret response', request=request)
    adapter = SocialPublisher(tmp_path / 'uncertain.db', transport=httpx.MockTransport(handler))
    with pytest.raises(ExtensionError):
        adapter.publish('Update', key='once')
    with pytest.raises(ExtensionError, match='uncertain'):
        adapter.publish('Update', key='once')
    assert len(calls) == 1


def test_credentials_not_in_httpx_logs(monkeypatch, caplog):
    import logging
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '123:secret')
    adapter = Telegram(owner_chat_id='123', transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={'ok': True, 'result': {'message_id': 1, 'chat': {'id': 123}}})))
    with caplog.at_level(logging.INFO, logger='httpx'):
        adapter.send('Hi')
    assert '123:secret' not in caplog.text


def test_voice_rejects_compressed_or_truncated_audio(tmp_path):
    import wave
    path = tmp_path / 'audio.wav'
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x00\x00' * 160)
    assert LocalVoice._audio(path) == path
    path.write_bytes(path.read_bytes()[:-4])
    with pytest.raises(ExtensionError):
        LocalVoice._audio(path)


@pytest.mark.parametrize('url', ['http://127.0.0.1/', 'https://example.com/',
                                 'https://secret@api.telegram.org/', 'https://api.telegram.org:8443/'])
def test_network_host_allowlist(url):
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json={}))
    with pytest.raises(ExtensionError):
        post(url, transport=transport)
    assert not calls


def test_real_ffmpeg_cut_and_caption(tmp_path):
    import shutil
    import subprocess

    from jarvis.extensions.creative import render_video
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg not installed')
    source, destination = tmp_path / 'input ;not-a-command.mp4', tmp_path / 'output.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=24:d=2', '-c:v', 'libx264',
                    '-threads', '1', '-pix_fmt', 'yuv420p', str(source)],
                   shell=False, check=True, timeout=15, capture_output=True)
    result = render_video(source, destination, duration=1, captions=[(0, 1, 'Profit +20.00')])
    assert result['duration'] == 1
    assert result['audio'] is False
    assert destination.stat().st_size > 1000
    with pytest.raises(ExtensionError):
        render_video(source, destination, start=1, duration=10)
