import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import pair_images


class PairImagesTests(unittest.TestCase):
    def test_global_assignment_does_not_make_greedy_mistake(self):
        scores = [[1, .95, .9, .1], [.95, 1, .1, .9], [.9, .1, 1, .1], [.1, .9, .1, 1]]
        self.assertEqual([p['indices'] for p in pair_images.optimal_pairs(scores)], [[0, 2], [1, 3]])

    def test_twenty_images_are_used_once_and_not_paired_by_filename_order(self):
        scores = [[1 if i == j else .95 if abs(i-j) == 10 else .1 for j in range(20)] for i in range(20)]
        pairs = pair_images.optimal_pairs(scores)
        self.assertEqual([p['indices'] for p in pairs], [[i, i+10] for i in range(10)])
        self.assertFalse(any(p['ambiguous'] for p in pairs))

    def test_similar_candidates_are_flagged_for_review(self):
        scores = [[1 if i == j else .9 for j in range(4)] for i in range(4)]
        self.assertTrue(all(p['ambiguous'] for p in pair_images.optimal_pairs(scores)))

    def test_invalid_batch_is_rejected(self):
        for matrix in [[], [[1]], [[1]*3]*3, [[1]*22]*22, [[1, float('nan')], [0, 1]]]:
            with self.subTest(matrix=matrix), self.assertRaises(ValueError):
                pair_images.optimal_pairs(matrix)

    def test_cached_model_works_offline_and_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            model = cache / 'model.onnx'
            model.write_bytes(b'checked-model')
            (cache / 'model.json').write_text(json.dumps({
                'repository': pair_images.MODEL_REPO,
                'sha256': hashlib.sha256(b'checked-model').hexdigest(), 'processor': {'size': 256}}))
            with patch('urllib.request.urlopen', side_effect=AssertionError('Offline cache must not access the network')):
                self.assertEqual(pair_images.ensure_model(cache)[0], model)
                model.write_bytes(b'tampered')
                with self.assertRaisesRegex(RuntimeError, 'integridade'):
                    pair_images.ensure_model(cache)

    def test_download_verifies_checksum_and_removes_partial_files(self):
        with tempfile.TemporaryDirectory() as folder:
            metadata = {'sha': 'a'*40, 'siblings': [{'rfilename': pair_images.MODEL_FILE,
                'lfs': {'sha256': hashlib.sha256(b'good').hexdigest(), 'size': 4}}]}
            with patch.object(pair_images, 'get_json', side_effect=[metadata, {}]), \
                    patch('urllib.request.urlopen', return_value=io.BytesIO(b'evil')):
                with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
                    pair_images.ensure_model(folder)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_network_failure_reports_no_image_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('blocked')):
                with self.assertRaisesRegex(RuntimeError, 'Nenhuma imagem foi enviada'):
                    pair_images.ensure_model(folder)

    def test_preprocessing_uses_local_decoder_and_expected_normalization(self):
        import numpy as np
        from types import SimpleNamespace
        image = bytes([255, 0, 0]) * (224*224)
        with patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=image)) as run:
            values = pair_images.decode_image('local image.png', {}, 'ffmpeg')
        self.assertEqual(values.shape, (1, 3, 224, 224))
        self.assertEqual(values.dtype, np.float32)
        self.assertAlmostEqual(float(values[0, 0, 0, 0]), (1-.485)/.229, places=5)
        self.assertIn('local image.png', run.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
