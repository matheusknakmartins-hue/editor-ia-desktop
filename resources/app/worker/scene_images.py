"""Offline scene-role suggestions with CLIP. Public models only; no image upload."""
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import urllib.error

REPOSITORY = 'Xenova/clip-vit-base-patch32'
SCENE_ONE = (
    'a photograph of a group of people presenting products at a table in a warehouse',
    'an advertisement with multiple presenters and stacks of products in a distribution center',
    'several people behind a display of products under a shop sign',
)
SCENE_TWO = (
    'a photograph of a single woman holding a product and presenting it to the camera in a store',
    'one woman showcasing a product in her hands in a retail aisle',
    'a female presenter alone demonstrating a product to the viewer',
)


class ClipTokenizer:
    """CLIP byte-level BPE for the fixed ASCII scene descriptions above."""
    def __init__(self, tokenizer):
        model = tokenizer['model']
        self.vocab = model['vocab']
        self.ranks = {}
        for rank, merge in enumerate(model['merges']):
            pair = tuple(merge.split(' ') if isinstance(merge, str) else merge)
            if len(pair) != 2:
                raise ValueError('Invalid tokenizer merge')
            self.ranks[pair] = rank
        values = list(range(ord('!'), ord('~')+1)) + list(range(161, 173)) + list(range(174, 256))
        characters = values[:]
        extra = 0
        for byte in range(256):
            if byte not in values:
                values.append(byte)
                characters.append(256+extra)
                extra += 1
        self.byte_encoder = dict(zip(values, map(chr, characters)))
        self.bos = self.vocab['<|startoftext|>']
        self.eos = self.vocab['<|endoftext|>']

    @lru_cache(maxsize=2048)
    def bpe(self, token):
        word = tuple(token[:-1]) + (token[-1] + '</w>',)
        while len(word) > 1:
            pairs = set(zip(word, word[1:]))
            best = min(pairs, key=lambda pair: self.ranks.get(pair, float('inf')))
            if best not in self.ranks:
                break
            merged = []
            index = 0
            while index < len(word):
                if index+1 < len(word) and (word[index], word[index+1]) == best:
                    merged.append(word[index] + word[index+1])
                    index += 2
                else:
                    merged.append(word[index])
                    index += 1
            word = tuple(merged)
        return word

    def encode(self, text):
        if not text.isascii():
            raise ValueError('Scene descriptions must be ASCII')
        tokens = [self.bos]
        for word in re.findall(r'[a-z]+|[0-9]|[^\s\w]+', text.lower()):
            encoded = ''.join(self.byte_encoder[byte] for byte in word.encode('utf-8'))
            tokens.extend(self.vocab[part] for part in self.bpe(encoded))
        if len(tokens) > 76:
            raise ValueError('Scene description is too long')
        tokens.append(self.eos)
        mask = [1] * len(tokens) + [0] * (77-len(tokens))
        return tokens + [self.eos] * (77-len(tokens)), mask


def load_tokenizer(cache):
    from pair_images import get_json, sha256
    manifest = Path(cache) / 'model.json'
    info = json.loads(manifest.read_text(encoding='utf-8'))
    target = Path(cache) / 'tokenizer.json'
    if target.exists() and info.get('tokenizer_sha256'):
        if sha256(target) != info['tokenizer_sha256']:
            raise RuntimeError('O identificador de cenas falhou na verificação de integridade. Remova a pasta models/clip-scenes dos dados do editor e tente novamente.')
        return ClipTokenizer(json.loads(target.read_text(encoding='utf-8')))
    revision = info['revision']
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise RuntimeError('A versão do identificador de cenas não foi confirmada.')
    try:
        data = get_json(f'https://huggingface.co/{REPOSITORY}/resolve/{revision}/tokenizer.json')
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('Não foi possível baixar o identificador de cenas. Conecte-se à internet no primeiro uso. Nenhuma imagem foi enviada.') from None
    tokenizer = ClipTokenizer(data)
    temporary = target.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(data), encoding='utf-8')
        os.replace(temporary, target)
        info['tokenizer_sha256'] = sha256(target)
        manifest.write_text(json.dumps(info), encoding='utf-8')
    finally:
        temporary.unlink(missing_ok=True)
    return tokenizer


def classify_scenes(files, cache, ffmpeg):
    import numpy as np
    import onnxruntime as ort
    from pair_images import ensure_model, decode_image, normalize, progress
    cache = Path(cache)
    progress('Preparando identificador local de cenas. No primeiro uso, baixa modelos gratuitos adicionais.')
    text_model, _ = ensure_model(cache / 'text', REPOSITORY, 'onnx/text_model_quantized.onnx')
    tokenizer = load_tokenizer(cache / 'text')
    ort.disable_telemetry_events()
    options = ort.SessionOptions()
    options.intra_op_num_threads = min(4, os.cpu_count() or 1)
    options.inter_op_num_threads = 1
    text_session = ort.InferenceSession(str(text_model), sess_options=options, providers=['CPUExecutionProvider'])
    encoded = [tokenizer.encode(prompt) for prompt in SCENE_ONE+SCENE_TWO]
    inputs = {'input_ids': np.array([item[0] for item in encoded], dtype=np.int64),
              'attention_mask': np.array([item[1] for item in encoded], dtype=np.int64)}
    names = [item.name for item in text_session.get_inputs()]
    vectors = text_session.run(['text_embeds'], {name: inputs[name] for name in names})[0]
    vectors = normalize(vectors)
    descriptions = np.stack([normalize(vectors[:len(SCENE_ONE)].mean(axis=0)),
                             normalize(vectors[len(SCENE_ONE):].mean(axis=0))])
    del text_session
    vision_model, processor = ensure_model(cache / 'vision', REPOSITORY, 'onnx/vision_model_quantized.onnx')
    vision_session = ort.InferenceSession(str(vision_model), sess_options=options, providers=['CPUExecutionProvider'])
    scores = []
    for index, file in enumerate(files, 1):
        progress(f'Identificando Cena 1 e Cenas 2/3 localmente: imagem {index}/{len(files)}…')
        pixels = decode_image(file, processor, ffmpeg, 'center')
        vector = normalize(vision_session.run(['image_embeds'], {'pixel_values': pixels})[0][0])
        similarities = descriptions @ vector
        scores.append(float(similarities[1]-similarities[0]))
    return scores
