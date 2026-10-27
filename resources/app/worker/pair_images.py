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


def optimal_pairs(similarities, scene_split=None):
    """Maximum-weight perfect matching, not a greedy nearest-neighbor choice."""
    count = len(similarities)
    if count < 2 or count > MAX_IMAGES or count % 2:
        raise ValueError('Selecione uma quantidade par de imagens, de 2 a 20.')
    if scene_split is not None and (type(scene_split) is not int or scene_split * 2 != count):
        raise ValueError('Selecione a mesma quantidade de imagens para cada cena.')
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
            if scene_split is not None and (first < scene_split) == (second < scene_split):
                choices ^= bit
                continue
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
                        if other not in (index, partner) and
                        (scene_split is None or (index < scene_split) != (other < scene_split))]
        score = similarities[first][second]
        result.append({'indices': [first, second], 'similarity': round(score, 4),
                       'ambiguous': score < 0.5 or bool(alternatives and score - max(alternatives) < 0.04)})
    return result


def decode_image(file, processor, ffmpeg, view='center'):
    import numpy as np
    size = processor.get('size', {}).get('shortest_edge', 256)
    crop = processor.get('crop_size', {'height': 224, 'width': 224})
    height, width = int(crop['height']), int(crop['width'])
    if not all(0 < dimension <= 1024 for dimension in (size, height, width)):
        raise RuntimeError('Dimensões do modelo visual não suportadas.')
    if view == 'center':
        filters = f'scale={size}:{size}:force_original_aspect_ratio=increase:flags=bicubic,crop={width}:{height}'
    elif view in ('full', 'product'):
        mean = processor.get('image_mean', [0.485, 0.456, 0.406])
        background = ''.join(f'{round(value * 255):02x}' for value in mean)
        # In this workflow the product is held or displayed below the presenters.
        # Keep the original views too: this crop is an additional cue, not a detector.
        prefix = 'crop=iw*0.9:ih*0.7:iw*0.05:ih*0.3,' if view == 'product' else ''
        filters = (prefix + f'scale={width}:{height}:force_original_aspect_ratio=decrease:flags=bicubic,'
                   f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x{background}')
    else:
        raise ValueError('Recorte de imagem não suportado.')
    decoded = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-i', str(file),
                              '-vf', filters, '-frames:v', '1', '-threads', '1',
                              '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True, timeout=60,
                             creationflags=0x08000000 if os.name == 'nt' else 0)
    if decoded.returncode or len(decoded.stdout) != height * width * 3:
        raise RuntimeError(f'Não foi possível abrir a imagem {Path(file).name}.')
    pixels = np.frombuffer(decoded.stdout, dtype=np.uint8).reshape(height, width, 3).astype(np.float32)
    pixels *= processor.get('rescale_factor', 1 / 255)
    mean = np.array(processor.get('image_mean', [0.485, 0.456, 0.406]), dtype=np.float32)
    std = np.array(processor.get('image_std', [0.229, 0.224, 0.225]), dtype=np.float32)
    return np.ascontiguousarray(((pixels - mean) / std).transpose(2, 0, 1)[None])


def normalize(values):
    import numpy as np
    norm = np.linalg.norm(values, axis=-1, keepdims=True)
    if not np.all(np.isfinite(norm)) or np.any(norm <= 0):
        raise RuntimeError('O modelo não conseguiu analisar os detalhes de uma imagem.')
    return values / norm


def detail_features(hidden, pixels, processor, view):
    """Keep spatial patch descriptors instead of discarding all but the CLS token."""
    import numpy as np
    if hidden.ndim != 3 or hidden.shape[0] != 1 or hidden.shape[1] <= 1:
        raise RuntimeError('O modelo instalado não retornou os detalhes visuais esperados.')
    global_vector = normalize(hidden[0, 0])
    patches = normalize(hidden[0, 1:])
    grid = math.isqrt(len(patches))
    height, width = pixels.shape[2:]
    if grid * grid != len(patches) or height % grid or width % grid:
        raise RuntimeError('A grade de detalhes do modelo visual não é compatível.')
    mean = np.array(processor.get('image_mean', [0.485, 0.456, 0.406]), dtype=np.float32)
    std = np.array(processor.get('image_std', [0.229, 0.224, 0.225]), dtype=np.float32)
    rgb = np.clip(pixels[0].transpose(1, 2, 0) * std + mean, 0, 1)
    cells = rgb.reshape(grid, height // grid, grid, width // grid, 3).transpose(0, 2, 1, 3, 4)
    cells = cells.reshape(len(patches), -1, 3)
    candidates = np.arange(len(patches))
    if view in ('full', 'product'):
        # Do not compare the artificial letterbox background as a product detail.
        padding = np.round(mean * 255) / 255
        content = np.mean(np.max(np.abs(cells - padding), axis=2) > 2/255, axis=1) > .5
        if np.any(content):
            candidates = candidates[content]
    salience = patches @ global_vector
    limit = 64 if view == 'product' else 24
    selected = candidates[np.argsort(salience[candidates], kind='stable')[-min(limit, len(candidates)):]]
    colors = np.minimum((cells[selected].reshape(-1, 3) * 8).astype(np.int32), 7)
    bins = colors[:, 0] * 64 + colors[:, 1] * 8 + colors[:, 2]
    histogram = np.bincount(bins, minlength=512).astype(np.float32)
    histogram /= histogram.sum()
    return {'global': global_vector, 'patches': patches[selected], 'colors': histogram}


def combine_views(views):
    import numpy as np
    weights = [.10, .25, .65]
    return {'global': normalize(np.average([view['global'] for view in views], axis=0, weights=weights)),
            'patches': np.concatenate([view['patches'] for view in views]),
            'colors': np.average([view['colors'] for view in views], axis=0, weights=weights)}


def similarity_matrix(features):
    """Compare local details and color as well as category-level appearance.

    Scores are ranking heuristics, not calibrated identity probabilities.
    """
    import numpy as np
    result = np.eye(len(features), dtype=np.float32)
    for first in range(len(features)):
        for second in range(first + 1, len(features)):
            a, b = features[first], features[second]
            global_score = float(np.clip(a['global'] @ b['global'], 0, 1))
            matches = np.clip(a['patches'] @ b['patches'].T, 0, 1)
            detail_score = float((matches.max(axis=0).mean() + matches.max(axis=1).mean()) / 2)
            color_score = float(np.sqrt(a['colors'] * b['colors']).sum())
            score = .30 * global_score + .55 * detail_score + .15 * color_score
            result[first, second] = result[second, first] = score
    return result.tolist()


def pair_images(files, cache, ffmpeg, scene_split=None):
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
    if 'last_hidden_state' not in names:
        raise RuntimeError('O modelo visual instalado não disponibiliza os detalhes das imagens.')
    features = []
    for index, file in enumerate(resolved, 1):
        progress(f'Análise visual detalhada local: imagem {index}/{len(files)}…')
        views = []
        for view in ('center', 'full', 'product'):
            pixels = decode_image(file, processor, ffmpeg, view)
            hidden = session.run(['last_hidden_state'], {session.get_inputs()[0].name: pixels})[0]
            views.append(detail_features(hidden, pixels, processor, view))
        features.append(combine_views(views))
    progress('Comparando detalhes e cores dos produtos…')
    return optimal_pairs(similarity_matrix(features), scene_split)


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
        payload = json.loads(Path(args.input).read_text(encoding='utf-8'))
        files = payload['files'] if isinstance(payload, dict) else payload
        split = payload.get('sceneSplit') if isinstance(payload, dict) else None
        result = pair_images(files, args.cache, binary('ffmpeg'), split)
        Path(args.output).write_text(json.dumps(result), encoding='utf-8')
    except Exception as error:
        print(f'Falha na análise local: {error}', file=sys.stderr)
        sys.exit(1)
