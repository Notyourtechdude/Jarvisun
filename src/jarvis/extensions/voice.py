"""Optional local whisper.cpp and Piper executable adapters; no model downloads."""

import shutil
import tempfile
import wave
from pathlib import Path

from .common import ExtensionError, bounded_text, output_file, regular_file
from .creative import _run


class LocalVoice:
    def __init__(self, *, whisper_executable=None, whisper_model=None,
                 piper_executable=None, piper_model=None):
        self.whisper_executable = self._executable(whisper_executable)
        self.whisper_model = Path(whisper_model) if whisper_model else None
        self.piper_executable = self._executable(piper_executable)
        self.piper_model = Path(piper_model) if piper_model else None

    @staticmethod
    def _executable(value):
        if value is None:
            return None
        path = Path(value)
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise ExtensionError('Configure an absolute regular executable path')
        return path

    def available(self):
        stt = bool(self.whisper_executable and self.whisper_model and self.whisper_model.is_file())
        tts = bool(self.piper_executable and self.piper_model and self.piper_model.is_file())
        return {'available': stt or tts, 'transcription': stt, 'synthesis': tts,
                'reason': 'Engines require local executables and authorized model files', 'live_verified': False}

    @staticmethod
    def _audio(path):
        path = regular_file(path, 20_000_000)
        try:
            with wave.open(str(path), 'rb') as audio:
                duration = audio.getnframes() / audio.getframerate()
                if (audio.getcomptype() != 'NONE' or audio.getnchannels() not in (1, 2)
                        or audio.getsampwidth() not in (1, 2) or not 8000 <= audio.getframerate() <= 48000
                        or not 0 < duration <= 120):
                    raise ExtensionError('Voice input must be bounded uncompressed PCM WAV')
                # Verify actual frame payload, not just untrusted RIFF metadata.
                expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
                if len(audio.readframes(audio.getnframes())) != expected:
                    raise ExtensionError('Voice WAV payload is truncated')
        except (wave.Error, EOFError, OSError, ZeroDivisionError):
            raise ExtensionError('Voice input must be valid uncompressed PCM WAV') from None
        return path

    def transcribe(self, source):
        if not self.available()['transcription']:
            raise ExtensionError('Local transcription engine or model is unavailable')
        source = self._audio(source).absolute()
        model = regular_file(self.whisper_model, 5_000_000_000).absolute()
        with tempfile.TemporaryDirectory(prefix='jarvis-voice-') as temp:
            prefix = Path(temp, 'transcript')
            _run([str(self.whisper_executable), '-m', str(model), '-f', str(source),
                  '-otxt', '-of', str(prefix), '-t', '2'], timeout=120)
            transcript = regular_file(Path(str(prefix) + '.txt'), 100_000).read_text(encoding='utf-8')
        return {'text': transcript.strip(), 'engine': 'whisper.cpp', 'verified': 'engine_output'}

    def synthesize(self, text, destination):
        if not self.available()['synthesis']:
            raise ExtensionError('Local synthesis engine or model is unavailable')
        text, destination = bounded_text(text, 1000), output_file(destination)
        if destination.suffix.lower() != '.wav':
            raise ExtensionError('Voice output must be WAV')
        model = regular_file(self.piper_model, 2_000_000_000).absolute()
        with tempfile.TemporaryDirectory(prefix='jarvis-voice-') as temp:
            staged = Path(temp, 'speech.wav')
            _run([str(self.piper_executable), '--model', str(model), '--output_file', str(staged)],
                 input_bytes=text.encode(), timeout=60)
            self._audio(staged)
            shutil.copyfile(staged, destination)
        return {'artifact': str(destination), 'kind': 'voice', 'engine': 'piper'}
