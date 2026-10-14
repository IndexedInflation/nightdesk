"""Bundle public screens and historical research into source and wheel installs."""
from pathlib import Path
from shutil import copyfile

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithAssets(build_py):
    def run(self):
        super().run()
        source = Path(__file__).resolve().parent
        assets = Path(self.build_lib) / "nightdesk" / "assets"
        for name, suffixes in (("ui", {".html", ".css", ".js"}), ("research", {".json"})):
            public = source / name
            for item in public.rglob("*"):
                if item.is_file() and not item.is_symlink() and item.suffix in suffixes:
                    target = assets / name / item.relative_to(public)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    copyfile(item, target)
        explainer = source / "How-Nightdesk-Works.html"
        if explainer.is_file():
            assets.mkdir(parents=True, exist_ok=True)
            (assets / explainer.name).write_bytes(explainer.read_bytes())


setup(cmdclass={"build_py": BuildWithAssets})
