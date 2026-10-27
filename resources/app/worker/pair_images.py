"""Local visual pairing. Only public model files are downloaded; images never leave the PC."""
import argparse
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

MODEL_REPO = 'Xenova/dinov2-small'
MODEL_FILE = 'onnx/model_quantized.onnx'
MAX_IMAGES = 20


def progress(message):
    print(message, flush=True)


def sha256(file):
    digest = hashlib.sha256()
    with open(file, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read(4 * 1024 * 1024))


def ensure_model(cache):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    model = cache / 'model.onnx'
    manifest = cache / 'model.json'
    if model.exists() and manifest.exists():
        info = json.loads(manifest.read_text(encoding='utf-8'))
        if info.get('repository') == MODEL_REPO and sha256(model) == info.get('sha256'):
            return model, info['processor']
        raise RuntimeError('O modelo local não passou na verificação de integridade. '
                           'Remova a pasta models/dinov2-small dos dados do editor e tente novamente.')
    progress('Primeiro uso: baixando modelo visual gratuito. Suas imagens não são enviadas.')
    try:
        metadata = get_json(f'https://huggingface.co/api/models/{MODEL_REPO}?blobs=true')
        revision = metadata['sha']
        if not re.fullmatch(r'[0-9a-f]{40}', revision):
            raise ValueError('Invalid model revision')
        entry = next(item for item in metadata['siblings'] if item['rfilename'] == MODEL_FILE)
        expected = entry['lfs']['sha256']
        size = entry['lfs']['size']
        if not re.fullmatch(r'[0-9a-f]{64}', expected) or not 0 < size < 200 * 1024 * 1024:
            raise ValueError('Invalid model metadata')
        base = f'https://huggingface.co/{MODEL_REPO}/resolve/{revision}'
        processor = get_json(base + '/preprocessor_config.json')
        descriptor, temporary = tempfile.mkstemp(prefix='download-', dir=cache)
        try:
            with os.fdopen(descriptor, 'wb') as target:
                with urllib.request.urlopen(base + '/' + MODEL_FILE, timeout=120) as source:
                    total = 0
                    last_report = -1
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        total += len(block)
                        if total > size:
                            raise ValueError('Unexpected model size')
                        target.write(block)
                        percentage = total * 100 // size
                        if percentage // 10 != last_report:
                            progress(f'Baixando modelo visual gratuito: {percentage}%')
                            last_report = percentage // 10
            if total != size or sha256(temporary) != expected:
                raise RuntimeError('O download do modelo falhou na verificação SHA-256. Tente novamente.')
            os.replace(temporary, model)
            manifest.write_text(json.dumps({'repository': MODEL_REPO, 'revision': revision,
                                            'sha256': expected, 'processor': processor}), encoding='utf-8')
        finally:
            Path(temporary).unlink(missing_ok=True)
        return model, processor
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('Não foi possível baixar o modelo gratuito do Hugging Face. '
                           'Conecte-se à internet no primeiro uso e tente novamente. '
                           'Nenhuma imagem foi enviada.') from None
    except (KeyError, StopIteration, ValueError):
        raise RuntimeError('Os arquivos do modelo visual não foram confirmados. '
                           'Não foi possível iniciar a análise local.') from None


def optimal_pairs(similarities):
    """Maximum-weight perfect matching, not a greedy nearest-neighbor choice."""
    count = len(similarities)
    if count < 2 or count > MAX_IMAGES or count % 2:
        raise ValueError('Selecione uma quantidade par de imagens, de 2 a 20.')
    if any(len(row) != count or any(not math.isfinite(x) for x in row) for row in similarities):
        raise ValueError('Não foi possível comparar as imagens.')

    @lru_cache(None)
    def solve(mask):
        if not mask:
            return 0, ()
        first_bit = mask & -mask
        first = first_bit.bit_length() - 1
        remaining = mask ^ first_bit
        choices = remaining
        best = (-math.inf, ())
        while choices:
            bit = choices & -choices
            second = bit.bit_length() - 1
            score, pairs = solve(remaining ^ bit)
            candidate = (score + similarities[first][second], ((first, second),) + pairs)
            if candidate[0] > best[0]:
                best = candidate
            choices ^= bit
        return best

    _, pairs = solve((1 << count) - 1)
    result = []
    for first, second in pairs:
        alternatives = [similarities[index][other] for index, partner in
                        ((first, second), (second, first)) for other in range(count)
                        if other not in (index, partner)]
        score = similarities[first][second]
        result.append({'indices': [first, second], 'similarity': round(score, 4),
                       'ambiguous': bool(alternatives and score - max(alternatives) < 0.04)})
    return result


def decode_image(file, processor, ffmpeg):
    import numpy as np
    size = processor.get('size', {}).get('shortest_edge', 256)
    crop = processor.get('crop_size', {'height': 224, 'width': 224})
    height, width = int(crop['height']), int(crop['width'])
    if not all(0 < dimension <= 1024 for dimension in (size, height, width)):
        raise RuntimeError('Dimensões do modelo visual não suportadas.')
    decoded = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-i', str(file),
                              '-vf', f'scale={size}:{size}:force_original_aspect_ratio=increase:flags=bicubic,'
                                     f'crop={width}:{height}', '-frames:v', '1', '-threads', '1',
                              '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True, timeout=60,
                             creationflags=0x08000000 if os.name == 'nt' else 0)
    if decoded.returncode or len(decoded.stdout) != height * width * 3:
        raise RuntimeError(f'Não foi possível abrir a imagem {Path(file).name}.')
    pixels = np.frombuffer(decoded.stdout, dtype=np.uint8).reshape(height, width, 3).astype(np.float32)
    pixels *= processor.get('rescale_factor', 1 / 255)
    mean = np.array(processor.get('image_mean', [0.485, 0.456, 0.406]), dtype=np.float32)
    std = np.array(processor.get('image_std', [0.229, 0.224, 0.225]), dtype=np.float32)
    return np.ascontiguousarray(((pixels - mean) / std).transpose(2, 0, 1)[None])


def pair_images(files, cache, ffmpeg):
    import numpy as np
    import onnxruntime as ort
    if len(files) < 2 or len(files) > MAX_IMAGES or len(files) % 2:
        raise ValueError('Selecione uma quantidade par de imagens, de 2 a 20.')
    resolved = [Path(file).resolve(strict=True) for file in files]
    if len(set(resolved)) != len(files) or any(not file.is_file() for file in resolved):
        raise ValueError('Selecione imagens diferentes, sem duplicar arquivos.')
    model, processor = ensure_model(cache)
    ort.disable_telemetry_events()
    options = ort.SessionOptions()
    options.intra_op_num_threads = min(4, os.cpu_count() or 1)
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
    names = [output.name for output in session.get_outputs()]
    output = 'pooler_output' if 'pooler_output' in names else 'last_hidden_state'
    vectors = []
    for index, file in enumerate(resolved, 1):
        progress(f'Análise visual local: imagem {index}/{len(files)}…')
        values = session.run([output], {session.get_inputs()[0].name: decode_image(file, processor, ffmpeg)})[0]
        vector = values[0] if values.ndim == 2 else values[0, 0]
        norm = np.linalg.norm(vector)
        if not np.isfinite(norm) or norm <= 0:
            raise RuntimeError('O modelo não conseguiu analisar uma das imagens.')
        vectors.append(vector / norm)
    matrix = np.stack(vectors)
    return optimal_pairs((matrix @ matrix.T).tolist())


if __name__ == '__main__':
    if os.name == 'nt':
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--cache', required=True)
    args = parser.parse_args()
    try:
        from edit_video import binary
        files = json.loads(Path(args.input).read_text(encoding='utf-8'))
        result = pair_images(files, args.cache, binary('ffmpeg'))
        Path(args.output).write_text(json.dumps(result), encoding='utf-8')
    except Exception as error:
        print(f'Falha na análise local: {error}', file=sys.stderr)
        sys.exit(1)
