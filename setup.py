"""Package the community runtime and genuine starter, never a commercial pack."""
from pathlib import Path
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent


class BuildCommunity(build_py):
    def run(self):
        super().run()
        destination = Path(self.build_lib) / 'nuluno_engine_community/growth_loop/_bundle'
        if destination.exists(): shutil.rmtree(destination)
        selected = [ROOT/name for name in ('LICENSE','NOTICE','THIRD_PARTY.md','community.json','skill-catalog.json')]
        for folder in ('growth-meta-ig-loop', 'growth-read-existing-offer'):
            selected += [p for p in (ROOT/'skills'/folder).rglob('*') if p.is_file() and not p.is_symlink()
                         and 'runtime' not in p.relative_to(ROOT/'skills'/folder).parts
                         and p.suffix in ('.json','.md')]
        selected += [p for p in (ROOT/'deploy/mcp-gateway').glob('*') if p.is_file() and not p.is_symlink() and p.suffix in ('.mjs','.json')]
        for source in selected:
            target = destination/source.relative_to(ROOT)
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)


setup(cmdclass={'build_py':BuildCommunity})
