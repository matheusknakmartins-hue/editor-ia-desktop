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
    def test_scene_groups_cannot_be_paired_within_the_same_scene(self):
        scores = [[1, .99, .80, .60], [.99, 1, .60, .80],
                  [.80, .60, 1, .99], [.60, .80, .99, 1]]
        pairs = pair_images.optimal_pairs(scores, scene_split=2)
        self.assertEqual([p['indices'] for p in pairs], [[0, 2], [1, 3]])
        self.assertFalse(any(p['ambiguous'] for p in pairs))

    def test_equal_category_vectors_can_be_separated_by_details_and_color(self):
        import numpy as np
        def features(kind):
            return {'global': np.array([1., 0, 0, 0, 0]),
                    'patches': np.eye(5)[[1, 2] if kind == 'pink' else [3, 4]],
                    'colors': np.array([1., 0]) if kind == 'pink' else np.array([0., 1])}
        images = [features(kind) for kind in ('pink', 'cream', 'pink', 'cream')]
        scores = pair_images.similarity_matrix(images)
        self.assertGreater(scores[0][2], scores[0][1])
        self.assertEqual([p['indices'] for p in pair_images.optimal_pairs(scores)], [[0, 2], [1, 3]])

    def test_low_similarity_is_marked_uncertain_even_with_no_alternatives(self):
        self.assertTrue(pair_images.optimal_pairs([[1, .2], [.2, 1]])[0]['ambiguous'])

    def test_full_and_product_views_preserve_aspect_ratio_instead_of_center_crop(self):
        from types import SimpleNamespace
        for view in ('full', 'product'):
            with self.subTest(view=view), patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=bytes(224*224*3))) as run:
                pair_images.decode_image('image.png', {}, 'ffmpeg', view)
                filters = run.call_args.args[0][run.call_args.args[0].index('-vf')+1]
                self.assertIn('force_original_aspect_ratio=decrease', filters)
                self.assertIn('pad=224:224', filters)

    def test_patch_descriptors_retain_local_features_and_color(self):
        import numpy as np
        hidden = np.ones((1, 5, 8), dtype=np.float32)
        pixels = np.zeros((1, 3, 224, 224), dtype=np.float32)
        features = pair_images.detail_features(hidden, pixels, {}, 'center')
        self.assertEqual(features['patches'].shape, (4, 8))
        self.assertAlmostEqual(float(features['colors'].sum()), 1)
        self.assertAlmostEqual(float(np.linalg.norm(features['global'])), 1, places=5)

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
