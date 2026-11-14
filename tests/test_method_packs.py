"""Real archive/SQLite regressions. Commercial data is never embedded here."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import zipfile

from nuluno_engine_community.growth_loop.method_packs import install_pack, active_library, _path
from nuluno_engine_community.growth_loop.resources import bundle_root


class PublicBoundaryTests(unittest.TestCase):
    def test_starter_catalog_is_real_and_paginated_without_paid_catalog_stubs(self):
        from nuluno_engine_community.growth_loop.library_catalog import page
        with mock.patch.dict(os.environ,{'NULUNO_METHOD_PACK_DIR':''}):
            work=page({});self.assertEqual(work['total'],1);self.assertEqual(work['items'][0]['id'],'S01')
            detail=page({'kind':'skill','skill_id':'S01','limit':10})
            self.assertEqual(detail['total_steps'],9);self.assertEqual(len(detail['steps']),2);self.assertEqual(detail['next_after'],2)
            self.assertIsNone(page({'kind':'goals'})['items'])
    def test_paths_and_code_cannot_enter_resource_library(self):
        for name in ('../escape', '/absolute', 'library\\skills\\escape',
                     'library/skills/growth-test/runtime/tool.py', 'service.json',
                     'library/skills/growth-test/agents/agent.json'):
            with self.subTest(name=name), self.assertRaises(ValueError): _path(name)

    def test_archive_hash_is_required_before_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/'data.zip';archive.write_bytes(b'not a zip')
            target=Path(tmp).resolve()/'packs'
            with self.assertRaisesRegex(ValueError,'operator_archive_sha256_required'):install_pack(archive,target,None,bundle_root())
            with self.assertRaisesRegex(ValueError,'pack_archive_hash_mismatch'):install_pack(archive,target,'0'*64,bundle_root())
            self.assertFalse(target.exists())


@unittest.skipUnless(os.environ.get('NULUNO_TEST_COMMERCIAL_PACK'),'separately licensed commercial ZIP not supplied')
class LicensedPackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.target=self.root/'packs'
        self.archive=Path(os.environ['NULUNO_TEST_COMMERCIAL_PACK']).resolve()
        self.sha=hashlib.sha256(self.archive.read_bytes()).hexdigest()
        with zipfile.ZipFile(self.archive) as zipped:self.files={i.filename:zipped.read(i) for i in zipped.infolist()}
        self.sentinel=self.root/'run/workflow.sqlite3';self.sentinel.parent.mkdir()
        db=sqlite3.connect(self.sentinel);db.execute('CREATE TABLE receipts(id PRIMARY KEY,body)');db.execute('INSERT INTO receipts VALUES(?,?)',('original','immutable'));db.commit();db.close()
        self.original_state=self.sentinel.read_bytes()
    def tearDown(self):self.tmp.cleanup()
    def install(self):return install_pack(self.archive,self.target,self.sha,bundle_root())
    def mutated(self,change):
        files=dict(self.files);manifest=json.loads(files['pack.json']);change(files,manifest)
        for name,data in files.items():
            if name!='pack.json':manifest['files'][name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        files['pack.json']=(json.dumps(manifest,ensure_ascii=False)+'\n').encode()
        path=self.root/'changed.zip'
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as zipped:
            for name,data in files.items():zipped.writestr(name,data)
        return path,hashlib.sha256(path.read_bytes()).hexdigest()
    def rejected_preserves_active(self,path,sha,reason):
        previous=(self.target/'active.json').read_bytes()
        with self.assertRaisesRegex(ValueError,reason):install_pack(path,self.target,sha,bundle_root())
        self.assertEqual((self.target/'active.json').read_bytes(),previous)
        self.assertEqual(self.sentinel.read_bytes(),self.original_state)
    def test_full_library_installs_reuses_and_leaves_run_database_unchanged(self):
        receipt=self.install();self.assertEqual(receipt['counts'],{'work_skills':186,'skill_folders':188,'goals':99,'graph_templates':134,'channel_skills':152})
        self.assertFalse(receipt['runtime_code_executed']);self.assertFalse(receipt['authority_modified'])
        again=self.install();self.assertEqual(again['archive_sha256'],self.sha)
        library,active=active_library(bundle_root(),self.target);self.assertEqual(active['counts']['goals'],99)
        self.assertEqual(self.sentinel.read_bytes(),self.original_state)
        from nuluno_engine_community.growth_loop.library_catalog import page
        with mock.patch.dict(os.environ,{'NULUNO_METHOD_PACK_DIR':str(self.target)}):
            catalog=page({'kind':'goals','limit':3});self.assertEqual(catalog['total'],99);self.assertEqual(len(catalog['items']),3)
    def test_bad_whole_hash_leaves_current_selection_untouched(self):
        self.install();self.rejected_preserves_active(self.archive,'0'*64,'pack_archive_hash_mismatch')
    def test_path_replacement_after_hash_cannot_change_verified_archive_bytes(self):
        local=self.root/'original.zip';local.write_bytes(self.archive.read_bytes())
        original_mkdtemp=tempfile.mkdtemp
        def replace_after_hash(*args,**kwargs):
            local.write_bytes(b'replaced after whole-archive validation')
            return original_mkdtemp(*args,**kwargs)
        with mock.patch('nuluno_engine_community.growth_loop.method_packs.tempfile.mkdtemp',side_effect=replace_after_hash):
            result=install_pack(local,self.target,self.sha,bundle_root())
        self.assertEqual(result['counts']['goals'],99)
        self.assertEqual(active_library(bundle_root(),self.target)[1]['archive_sha256'],self.sha)
    def test_rehashed_unknown_graph_dependency_rejected(self):
        self.install()
        def change(files,manifest):
            name=next(n for n in files if '/graphs/' in n);graph=json.loads(files[name]);graph['nodes'][0]['skill_id']='S-NOT-INSTALLED'
            files[name]=json.dumps(graph).encode()
        path,sha=self.mutated(change);self.rejected_preserves_active(path,sha,'pack_unknown_graph_dependency')
    def test_rehashed_schema_or_authority_escalation_rejected(self):
        self.install();path,sha=self.mutated(lambda files,m:m.update(authority={'runs':['*'],'spend':'unlimited'}))
        self.rejected_preserves_active(path,sha,'pack_unknown_manifest_fields')
    def test_rehashed_path_traversal_rejected_without_outside_write(self):
        self.install();path,sha=self.mutated(lambda files,m:files.update({'../escape':b'forbidden'}))
        self.rejected_preserves_active(path,sha,'pack_unsafe_path');self.assertFalse((self.root/'escape').exists())
    def test_rehashed_runtime_code_rejected(self):
        self.install();path,sha=self.mutated(lambda files,m:files.update({'library/skills/growth-test/runtime/code.py':b'raise RuntimeError()'}))
        self.rejected_preserves_active(path,sha,'pack_code_or_authority_file_forbidden')
    def test_starter_contract_change_rejected(self):
        self.install()
        def change(files,m):
            name='library/skills/growth-meta-ig-loop/references/blueprint.json';data=json.loads(files[name]);data['skills'][0]['responsibility']='Changed pending work';files[name]=json.dumps(data).encode()
        path,sha=self.mutated(change);self.rejected_preserves_active(path,sha,'pack_starter_contract_changed')
    def test_controlled_upgrade_is_atomic_and_downgrade_is_not_implicit(self):
        self.install();path,sha=self.mutated(lambda files,m:m.update(pack_version='0.12.1'))
        result=install_pack(path,self.target,sha,bundle_root());self.assertEqual(result['pack_version'],'0.12.1')
        self.rejected_preserves_active(self.archive,self.sha,'pack_downgrade_requires_explicit_review')
    def test_installed_file_tamper_is_not_trusted_on_restart(self):
        self.install();library,_=active_library(bundle_root(),self.target)
        p=library/'skills/growth-read-existing-offer/references/procedure.md';p.write_text('tampered')
        with self.assertRaisesRegex(ValueError,'pack_file_hash_mismatch'):active_library(bundle_root(),self.target)
        self.assertEqual(self.sentinel.read_bytes(),self.original_state)


if __name__=='__main__':unittest.main()
