"""Local, conservative editing. Never changes the downloaded source clips."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

if os.name == 'nt':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parents[1]
SPEEDS = (1.30, 1.15, 1.15)


def voice_endpoint(probabilities, duration):
    """Hysteresis with zero padding; confirmation time is never added to the cut."""
    active = False
    pending = None
    last_end = None
    for index, probability in enumerate(probabilities):
        when = index * 0.032
        if probability >= 0.5:
            active = True
            pending = None
        elif active and probability < 0.35:
            if pending is None:
                pending = when
            if when - pending >= 0.128:
                last_end = pending
                active = False
                pending = None
    if active:
        last_end = pending if pending is not None else duration
    if last_end is None or last_end < 0.5:
        raise RuntimeError('Não foi possível localizar o fim da fala com confiança.')
    return min(duration, last_end)


def speech_end(clip, duration):
    sys.path.insert(0, str(ROOT / 'vendor' / 'vad'))
    import numpy as np
    import onnxruntime as ort
    ort.disable_telemetry_events()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(ROOT / 'assets' / 'silero-vad.onnx'),
                                   sess_options=options, providers=['CPUExecutionProvider'])
    decoded = subprocess.run([binary('ffmpeg'), '-v', 'error', '-nostdin', '-i', str(clip),
                              '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-'],
                             capture_output=True, check=True, timeout=60)
    samples = np.frombuffer(decoded.stdout, dtype=np.float32)
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, 64), dtype=np.float32)
    probabilities = []
    for start in range(0, len(samples), 512):
        chunk = samples[start:start + 512]
        chunk = np.pad(chunk, (0, 512 - len(chunk))).reshape(1, 512)
        inputs = np.concatenate((context, chunk), axis=1)
        probability, state = session.run(None, {'input': inputs, 'state': state,
                                               'sr': np.array(16000, dtype=np.int64)})
        probabilities.append(float(probability.item()))
        context = inputs[:, -64:]
    return voice_endpoint(probabilities, duration)


def binary(name):
    env_name = 'VIDEO_FACTORY_' + name.upper()
    if os.name == 'nt':
        name = name + '.exe'
    candidates = [os.environ.get(env_name),
                  str(ROOT / 'bin' / name), shutil.which(name),
                  '/Applications/Editor NXT.app/Contents/Resources/vendor/bin/' + name]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError(f'{name} não encontrado. Configure VIDEO_FACTORY_{name.upper()}.')


def run(command, timeout=480):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=0x08000000 if os.name == 'nt' else 0, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr[-5000:] or result.stdout[-5000:])
    return result


def probe(path):
    return json.loads(run([binary('ffprobe'), '-v', 'error', '-show_streams',
                           '-show_format', '-of', 'json', str(path)]).stdout)


def tail_cut(log, duration, margin=0.16):
    """Only terminal silence; never delete pauses inside the spoken sentence."""
    intervals = []
    start = None
    for kind, value in re.findall(r'silence_(start|end):\s*([\d.]+)', log):
        if kind == 'start':
            start = float(value)
        elif start is not None:
            intervals.append((start, float(value)))
            start = None
    if start is not None:
        intervals.append((start, duration))
    for start, end in reversed(intervals):
        # Small codec tail clicks may follow the final silence.
        if end >= duration - 0.10 and end - start >= 0.25 and start >= 0.5:
            return min(duration, start + margin)
    return duration


def edit(output, clips, speeds=SPEEDS, assembly_key=None, cache_dir=None):
    if len(speeds) != 3 or any(not 0.5 <= float(s) <= 2 for s in speeds):
        raise ValueError('Escolha três velocidades entre 0,50 e 2,00.')
    if len(clips) != 3:
        raise ValueError('A edição exige exatamente as três cenas.')
    ffmpeg = binary('ffmpeg')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    arrow = ROOT / 'assets' / 'seta-carrinho.gif'
    if not arrow.is_file():
        raise RuntimeError('Seta aprovada não encontrada.')
    report = {'speeds': speeds, 'cutMethod': 'assemblyai_last_word' if assembly_key else 'silero_v6.2.1_voice_endpoint',
              'safetyMarginSeconds': 0,
              'scenes': [], 'arrowSource': 'https://giphy.com/stickers/xELuuAj5u5tyX2MaLw'}
    started = time.monotonic()
    stage_times = {}
    with tempfile.TemporaryDirectory(prefix='.editing-', dir=output.parent) as directory:
        temp = Path(directory)
        analysis_started = time.monotonic()
        def analyze(item):
            index, clip, speed = item
            print(f'Analisando fala da cena {index}/3…', flush=True)
            scene_started = time.monotonic()
            info = probe(clip)
            video = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
            audio = next((s for s in info['streams'] if s['codec_type'] == 'audio'), None)
            if not video or not audio:
                raise RuntimeError(f'Cena {index} precisa conter vídeo e áudio.')
            duration = min(float(video.get('duration', info['format']['duration'])),
                           float(audio.get('duration', info['format']['duration'])))
            assembly_info = None
            if assembly_key:
                from assembly_cut import transcribe_end
                end, assembly_info = transcribe_end(clip, duration, assembly_key,
                    cache_dir or output.parent / 'transcripts', ffmpeg, run)
            else:
                end = speech_end(clip, duration)
            voice_end = end
            if index == 2 and not assembly_key:
                detection = run([ffmpeg, '-hide_banner', '-nostdin', '-i', str(clip), '-vn',
                                 '-af', 'highpass=f=200,lowpass=f=4000,silencedetect=n=-25dB:d=0.25',
                                 '-f', 'null', '-'])
                end = min(end, tail_cut(detection.stderr, duration, margin=0))
            return {'scene': index, 'source': str(Path(clip).resolve()),
                    'originalSeconds': duration, 'cutEndSeconds': end,
                    'voiceEndSeconds': voice_end, 'speed': speed, 'editedSeconds': end / speed,
                    'trimmedSeconds': duration - end, 'assemblyAI': assembly_info,
                    'analysisSeconds': round(time.monotonic() - scene_started, 3)}
        items = [(i, clip, speed) for i, (clip, speed) in enumerate(zip(clips, speeds), 1)]
        if assembly_key:
            from concurrent.futures import ThreadPoolExecutor
            # Two short audio jobs at most; products remain sequential.
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(analyze, item) for item in items]
                try:
                    report['scenes'] = [future.result() for future in futures]
                except Exception:
                    for future in futures:
                        future.cancel()
                    raise
        else:
            report['scenes'] = [analyze(item) for item in items]
        stage_times['analysisSeconds'] = round(time.monotonic() - analysis_started, 3)
        print('Renderizando: cortes, velocidade, áudio e seta em uma única passagem…', flush=True)
        filters = []
        for i, scene in enumerate(report['scenes']):
            end, speed, target = scene['cutEndSeconds'], scene['speed'], scene['editedSeconds']
            filters.append(
                f'[{i}:v]trim=end={end},setpts=(PTS-STARTPTS)/{speed},'
                'scale=720:1280:force_original_aspect_ratio=decrease,'
                f'pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v{i}]')
            filters.append(
                f'[{i}:a]atrim=end={end},asetpts=PTS-STARTPTS,highpass=f=70,'
                f'afftdn=nr=8:nf=-40,atempo={speed},aresample=48000,'
                f'aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=duration={target}[a{i}]')
        filters.append('[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[joined][a]')
        filters.append('[3:v]format=rgba,rotate=-3*PI/4:ow=rotw(-3*PI/4):'
                       'oh=roth(-3*PI/4):c=none,crop=160:600:250:60,scale=56:210[arrow]')
        filters.append('[joined][arrow]overlay=x=63:y=970:shortest=1[v]')
        staged = temp / 'final.mp4'
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n']
        for clip in clips:
            command.extend(['-threads', '2', '-i', str(clip)])
        command.extend(['-ignore_loop', '0', '-i', str(arrow),
                        '-filter_complex_threads', '2', '-filter_complex', ';'.join(filters),
                        '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-threads', '2',
                        '-preset', 'fast', '-crf', '19', '-pix_fmt', 'yuv420p', '-r', '30',
                        '-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-movflags', '+faststart', str(staged)])
        render_started = time.monotonic()
        run(command)
        stage_times['renderSeconds'] = round(time.monotonic() - render_started, 3)
        validation_started = time.monotonic()
        final_info = probe(staged)
        actual = float(final_info['format']['duration'])
        expected = sum(s['editedSeconds'] for s in report['scenes'])
        if abs(actual - expected) > 0.5:
            raise RuntimeError(f'Duração final inesperada: {actual:.2f}s, esperado {expected:.2f}s')
        run([ffmpeg, '-v', 'error', '-xerror', '-nostdin', '-i', str(staged), '-f', 'null', '-'])
        stage_times['validationSeconds'] = round(time.monotonic() - validation_started, 3)
        report.update(outputSeconds=actual, processingSeconds=round(time.monotonic() - started, 2),
                      timings=stage_times, renderPipeline='single_pass')
        # Publish only a completely rendered and decoded video.
        os.replace(staged, output)
        output.with_suffix('.editing.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return report


if __name__ == '__main__':
    try:
        assembly_mode = '--assemblyai' in sys.argv
        if assembly_mode:
            sys.argv.remove('--assemblyai')
        if len(sys.argv) not in (5, 8):
            raise ValueError('Uso: edit_video.py saida.mp4 cena1.mp4 cena2.mp4 cena3.mp4')
        key = sys.stdin.readline().strip() if assembly_mode else None
        if assembly_mode and not key:
            raise ValueError('API key do AssemblyAI não informada. Nenhum áudio enviado.')
        edit(sys.argv[1], sys.argv[2:5], tuple(map(float, sys.argv[5:8])) if len(sys.argv) == 8 else SPEEDS, assembly_key=key)
    except Exception as error:
        print(f'Falha na edição: {error}', file=sys.stderr)
        sys.exit(1)
