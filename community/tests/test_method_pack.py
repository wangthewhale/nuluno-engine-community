import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from method_pack import inspect, load_json

class PackageBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'proposal'
        shutil.copytree(Path(__file__).resolve().parents[1] / 'examples/normalize-query', self.root)

    def tearDown(self):
        self.temp.cleanup()

    def change_manifest(self, update):
        p = self.root / 'method.json'
        value = json.loads(p.read_text())
        update(value)
        p.write_text(json.dumps(value))

    def test_valid_package_stays_unadopted_and_does_not_authorize_operations(self):
        result = inspect(self.root)
        self.assertEqual(result['integrity'], 'valid')
        self.assertFalse(result['adopted'])
        self.assertFalse(result['external_execution_authorized'])
        self.assertFalse(result['evidence_authenticity_verified'])

    def test_altered_payload_and_extra_private_files_are_rejected(self):
        (self.root / 'program.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            inspect(self.root)
        self.setUp_extra_file_check()

    def setUp_extra_file_check(self):
        shutil.rmtree(self.root)
        shutil.copytree(Path(__file__).resolve().parents[1] / 'examples/normalize-query', self.root)
        (self.root / '.env').write_text('PRIVATE=not-for-publication')
        with self.assertRaisesRegex(ValueError, 'unlisted_file'):
            inspect(self.root)

    def test_traversal_and_symlink_files_are_rejected(self):
        self.change_manifest(lambda m: m['files'].update({'../outside': 'a' * 64}))
        with self.assertRaisesRegex(ValueError, 'unsafe_path'):
            inspect(self.root)
        self.change_manifest(lambda m: m['files'].pop('../outside'))
        p = self.root / 'program.json'
        target = self.root.parent / 'target'
        target.write_bytes(p.read_bytes())
        p.unlink()
        p.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            inspect(self.root)

    def test_package_cannot_grant_permissions(self):
        self.change_manifest(lambda m: m.update(requested_permissions=['mail.read']))
        with self.assertRaisesRegex(ValueError, 'separate_host_review'):
            inspect(self.root)

    def test_duplicate_json_keys_are_not_accepted(self):
        with self.assertRaisesRegex(ValueError, 'duplicate_json_key'):
            load_json('{"scope":[],"scope":["write"]}')

if __name__ == '__main__':
    unittest.main()
