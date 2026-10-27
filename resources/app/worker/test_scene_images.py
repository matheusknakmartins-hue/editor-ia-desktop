import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scene_images import ClipTokenizer, classify_scenes, load_tokenizer


def tiny_tokenizer():
    return {'model': {'vocab': {'<|startoftext|>': 1, '<|endoftext|>': 2,
            'a</w>': 3, 'photo</w>': 9},
            'merges': ['p h', 'ph o', 'pho t', 'phot o</w>']}}


class SceneImageTests(unittest.TestCase):
    def test_clip_bpe_and_special_tokens_padding_attention_mask(self):
        tokenizer = ClipTokenizer(tiny_tokenizer())
        tokens, mask = tokenizer.encode('A photo')
        self.assertEqual(tokens[:4], [1, 3, 9, 2])
        self.assertEqual(mask[:4], [1]*4)
        self.assertEqual(tokens[4:], [2]*73)
        self.assertEqual(mask[4:], [0]*73)
        self.assertEqual(len(tokens), 77)

    def test_cached_tokenizer_works_offline_and_verifies_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            text = json.dumps(tiny_tokenizer())
            (cache / 'tokenizer.json').write_text(text, encoding='utf-8')
            (cache / 'model.json').write_text(json.dumps({'tokenizer_sha256': hashlib.sha256(text.encode()).hexdigest()}))
            with patch('urllib.request.urlopen', side_effect=AssertionError('Must remain offline')):
                self.assertEqual(load_tokenizer(cache).encode('a photo')[0][:4], [1, 3, 9, 2])
                (cache / 'tokenizer.json').write_text('{}')
                with self.assertRaisesRegex(RuntimeError, 'integridade'):
                    load_tokenizer(cache)

    def test_local_inference_connects_text_and_image_models_without_image_upload(self):
        import numpy as np
        class Session:
            def __init__(self, model, **kwargs):
                self.text = str(model) == 'text-model'
                self.index = 0
            def get_inputs(self):
                return [SimpleNamespace(name=name) for name in ('input_ids', 'attention_mask')]
            def run(self, outputs, inputs):
                if self.text:
                    self_test.assertEqual(outputs, ['text_embeds'])
                    self_test.assertEqual(inputs['input_ids'].shape, (6, 77))
                    return [np.array([[1., 0]]*3 + [[0., 1]]*3)]
                self_test.assertEqual(outputs, ['image_embeds'])
                self_test.assertEqual(inputs['pixel_values'].shape, (1, 3, 224, 224))
                self.index += 1
                return [np.array([[0., 1]] if self.index == 1 else [[1., 0]])]
        self_test = self
        tokenizer = SimpleNamespace(encode=lambda text: ([1]*77, [1]*77))
        with patch('pair_images.ensure_model', side_effect=[('text-model', {}), ('vision-model', {})]), \
                patch('scene_images.load_tokenizer', return_value=tokenizer), \
                patch('pair_images.decode_image', return_value=np.zeros((1, 3, 224, 224), dtype=np.float32)), \
                patch('onnxruntime.InferenceSession', Session), patch('urllib.request.urlopen', side_effect=AssertionError('No uploads')):
            scores = classify_scenes(['image-a.png', 'image-b.png'], 'cache', 'ffmpeg')
        self.assertEqual(scores, [1., -1.])


if __name__ == '__main__':
    unittest.main()
