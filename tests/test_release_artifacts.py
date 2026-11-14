"""Release boundary regressions; no publishing, model or provider operations."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_artifacts',ROOT/'scripts/release_artifacts.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)


class ReleaseBoundaries(unittest.TestCase):
    def test_private_data_commercial_libraries_and_new_skill_folders_fail_closed(self):
        for path in ('.private/state.json','docs/.env','credentials.json','docs/raw.sqlite3','skills/growth-meta-ig-loop/references/goal-catalog.json',
                     'skills/growth-meta-ads/SKILL.md','../outside','/absolute','docs\\private.txt'):
            with self.subTest(path=path),self.assertRaises(ValueError):release.safe_path(path)
        self.assertEqual(release.safe_path('community/examples/normalize-query/program.json'),'community/examples/normalize-query/program.json')
    def test_only_reviewed_learning_demo_is_exportable_under_examples(self):
        self.assertEqual(release.safe_path('examples/method-learning/README.md'),'examples/method-learning/README.md')
        for path in ('examples/unreviewed/program.py','examples/method-learning/private/source.json','examples/method-learning/source.sqlite3'):
            with self.subTest(path=path),self.assertRaises(ValueError):release.safe_path(path)
    def test_version_and_tag_cannot_relabel_a_different_runtime(self):
        version='0.13.0a1'
        files={'pyproject.toml':b'[project]\nname="nuluno-engine-community"\nversion="0.13.0a1"\n',
               'community_namespace/__init__.py':b'__version__="0.13.0a1"\n',
               'skills/growth-meta-ig-loop/runtime/growth_loop/__init__.py':b'__version__="0.13.0a1"\n',
               'community.json':json.dumps({'version':version}).encode()}
        self.assertEqual(release.version_of(files,version,'community-v'+version),version)
        with self.assertRaisesRegex(ValueError,'requested_version'):release.version_of(files,'0.13.0')
        with self.assertRaisesRegex(ValueError,'tag_'):release.version_of(files,version,'community-v0.13.0')
        files['community_namespace/__init__.py']=b'__version__="0.12.0"\n'
        with self.assertRaisesRegex(ValueError,'runtime_version'):release.version_of(files,version)
    def test_snapshot_reads_committed_blobs_not_dirty_or_untracked_private_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            subprocess.run(['git','init','-q',str(root)],check=True)
            for key,value in [('user.name','Controlled Test'),('user.email','nobody@example.invalid')]:subprocess.run(['git','-C',str(root),'config',key,value],check=True)
            blueprint=root/'skills/growth-meta-ig-loop/references/blueprint.json';blueprint.parent.mkdir(parents=True)
            blueprint.write_text(json.dumps({'skills':[{'id':'S01'}],'workflows':[]}));(root/'README.md').write_text('committed')
            subprocess.run(['git','-C',str(root),'add','.'],check=True);subprocess.run(['git','-C',str(root),'commit','-qm','controlled fixture'],check=True)
            (root/'README.md').write_text('dirty content');(root/'.env').write_text('PRIVATE=do-not-export')
            with mock.patch.object(release,'ROOT',root):
                _,files=release.read_snapshot()
            self.assertEqual(files['README.md'],b'committed');self.assertNotIn('.env',files)
            target=root/'linked';target.symlink_to('README.md')
            subprocess.run(['git','-C',str(root),'add','linked'],check=True);subprocess.run(['git','-C',str(root),'commit','-qm','controlled invalid link'],check=True)
            with mock.patch.object(release,'ROOT',root),self.assertRaisesRegex(ValueError,'links_and_submodules'):release.read_snapshot()


if __name__=='__main__':unittest.main()
