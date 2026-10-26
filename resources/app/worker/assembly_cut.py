"""AssemblyAI word endpoints. Credentials arrive over stdin, never CLI or files."""
import hashlib
import json
import math
from pathlib import Path
import re
import time
import threading
import urllib.error
import urllib.request

BASE = 'https://api.assemblyai.com/v2'
MODELS = ['universal-3-pro', 'universal-2']
# Small alignment overshoots are clamped to the real media endpoint below.
ENDPOINT_TOLERANCE_MS = 250
_locks_guard = threading.Lock()
_locks = {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward Authorization to another host.


def request(method, path, key, data=None, binary=False):
    body = data if binary else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(BASE + path, data=body, method=method, headers={
        'Authorization': key,
        'Content-Type': 'application/octet-stream' if binary else 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        messages = {401: 'API key inválida.', 403: 'Acesso negado. Confira a chave e o projeto AssemblyAI.',
                    402: 'Verifique o saldo e o faturamento AssemblyAI.',
                    429: 'Limite AssemblyAI atingido. Aguarde antes de tentar novamente.'}
        raise RuntimeError(messages.get(exc.code, f'AssemblyAI respondeu HTTP {exc.code}.')) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise RuntimeError('Falha de conexão ou resposta inválida do AssemblyAI. Não foi feita nova submissão automática.') from None


def endpoint(transcript, duration):
    words = transcript.get('words') or []
    if not words:
        raise RuntimeError('AssemblyAI não reconheceu palavras; corte automático interrompido.')
    for index, word in enumerate(words, 1):
        start, end = word.get('start'), word.get('end')
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start <= end):
            raise RuntimeError(f'AssemblyAI retornou tempos de palavras inválidos na palavra {index}: '
                               f'início={start!r}, fim={end!r} ms.')
        if end > duration * 1000 + ENDPOINT_TOLERANCE_MS:
            raise RuntimeError(f'AssemblyAI retornou tempo além da cena na palavra {index}: '
                               f'fim={end:.0f} ms, duração={duration * 1000:.0f} ms.')
    # Alignment can produce zero-duration words or overlapping/out-of-order
    # entries. Only the latest endpoint is needed; do not depend on list order.
    last = max(words, key=lambda word: word['end'])
    if last['end'] <= 0:
        raise RuntimeError('AssemblyAI não localizou um fim de fala maior que zero.')
    if last.get('confidence', 0) < 0.5:
        raise RuntimeError('Última palavra com baixa confiança. Revise antes de cortar.')
    return min(duration, last['end'] / 1000), {
        'transcriptId': transcript.get('id'), 'model': transcript.get('speech_model_used'),
        'lastWord': last.get('text'), 'lastWordConfidence': last.get('confidence'),
        'lastWordEndMilliseconds': last['end']}


def transcribe_end(clip, duration, key, cache_dir, ffmpeg, run):
    started = time.monotonic()
    if not key or any(c.isspace() for c in key):
        raise RuntimeError('Informe uma API key válida no aplicativo.')
    import tempfile
    with tempfile.TemporaryDirectory(prefix='editor-audio-') as folder:
        audio = Path(folder) / 'audio.wav'
        # Match the timeline that edit_video can render (the shorter stream).
        # Audio tracks can extend beyond the video stream in valid input files.
        run([ffmpeg, '-v', 'error', '-nostdin', '-i', str(clip), '-vn',
             '-af', 'asetpts=PTS-STARTPTS', '-t', str(duration), '-ac', '1',
             '-ar', '16000', '-c:a', 'pcm_s16le', str(audio)])
        data = audio.read_bytes()
    extracted = time.monotonic()
    # Cache limited to this app session; changing speeds does not incur another transcription.
    digest = hashlib.sha256(data + json.dumps(MODELS).encode() + key.encode()).hexdigest()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = cache_dir / (digest + '.json')
    with _locks_guard:
        lock = _locks.setdefault(str(record.resolve()), threading.Lock())
    # Identical audio used twice in a product must not create duplicate paid jobs.
    with lock:
        end, info = _transcribe_record(record, data, duration, key)
    info['totalSeconds'] = round(time.monotonic() - started, 3)
    info['extractSeconds'] = round(extracted - started, 3)
    return end, info


def _transcribe_record(record, data, duration, key):
    timings = {'uploadSeconds': 0, 'submitSeconds': 0, 'waitSeconds': 0, 'cacheHit': False}
    state = json.loads(record.read_text()) if record.exists() else {}
    if state.get('status') == 'completed':
        print('AssemblyAI: reutilizando transcrição desta sessão.', flush=True)
        end, info = endpoint(state, duration)
        timings['cacheHit'] = True
        info.update(timings)
        return end, info
    if not state.get('id'):
        if state.get('status') == 'submitting':
            raise RuntimeError('Envio anterior sem confirmação. Confira o painel AssemblyAI antes de abrir outra sessão e reenviar.')
        print('AssemblyAI: enviando somente o áudio…', flush=True)
        tick = time.monotonic()
        uploaded = request('POST', '/upload', key, data, binary=True)
        timings['uploadSeconds'] = round(time.monotonic() - tick, 3)
        audio_url = uploaded.get('upload_url')
        if not isinstance(audio_url, str) or not audio_url.startswith('https://'):
            raise RuntimeError('AssemblyAI não confirmou o upload.')
        record.write_text(json.dumps({'status': 'submitting'}))
        tick = time.monotonic()
        state = request('POST', '/transcript', key, {'audio_url': audio_url,
                        'speech_models': MODELS, 'language_code': 'pt'})
        timings['submitSeconds'] = round(time.monotonic() - tick, 3)
        if not re.fullmatch(r'[A-Za-z0-9-]+', state.get('id', '')):
            raise RuntimeError('AssemblyAI não confirmou a identificação da transcrição.')
        record.write_text(json.dumps({'id': state['id'], 'status': 'queued'}))
    deadline = time.monotonic() + 600
    wait_started = time.monotonic()
    while time.monotonic() < deadline:
        print('AssemblyAI: aguardando transcrição e tempos das palavras…', flush=True)
        state = request('GET', '/transcript/' + state['id'], key)
        if state.get('status') == 'completed':
            result = endpoint(state, duration)
            timings['waitSeconds'] = round(time.monotonic() - wait_started, 3)
            result[1].update(timings)
            # Store only needed results, not audio URL or credentials.
            record.write_text(json.dumps({k: state.get(k) for k in
                              ('id', 'status', 'words', 'speech_model_used')}, ensure_ascii=False))
            return result
        if state.get('status') == 'error':
            raise RuntimeError('AssemblyAI não conseguiu transcrever. Consulte o processamento no painel AssemblyAI; não houve fallback local.')
        time.sleep(2)
    raise RuntimeError('AssemblyAI demorou mais de 10 minutos. Tente a prévia novamente nesta sessão para consultar a mesma transcrição, sem reenviar.')
