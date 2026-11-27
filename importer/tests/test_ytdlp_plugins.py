"""yt-dlp plugin folders: custom or fixed site extractors loaded on every yt-dlp call."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from vsimporter.adapters import RealYtDlp, parse_plugins
from vsimporter.config import Settings

pytestmark = pytest.mark.anyio

needs_ytdlp = pytest.mark.skipif(shutil.which("yt-dlp") is None, reason="yt-dlp not installed")

# A plugin for a made-up URL scheme, so the test needs no network.
DEMO = textwrap.dedent('''
    from yt_dlp.extractor.common import InfoExtractor


    class TastebudDemoIE(InfoExtractor):
        _VALID_URL = r'tastebud-demo:(?P<id>\\w+)'

        def _real_extract(self, url):
            video_id = self._match_id(url)
            return {'id': video_id, 'title': 'Demo ' + video_id, 'url': 'https://example.invalid/v.mp4', 'ext': 'mp4'}
''')


def make_plugin(root: Path, name: str = "demo.py", code: str = DEMO) -> Path:
    ext = root / "tastebud" / "yt_dlp_plugins" / "extractor"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / name).write_text(code, "utf-8")
    return root


def settings_with(tmp_path: Path, *dirs: Path | str) -> Settings:
    return Settings(data_dir=tmp_path, ytdlp_cookies_file="", ytdlp_plugin_dirs=",".join(str(d) for d in dirs))


def test_existing_plugin_dirs_are_passed_and_missing_ones_skipped(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    base = RealYtDlp(settings_with(tmp_path, a, b))._base()
    assert base[base.index("--plugin-dirs") + 1] == str(a)
    assert str(b) not in base


def test_no_plugin_dirs_disables_plugin_search(tmp_path):
    base = RealYtDlp(settings_with(tmp_path, tmp_path / "missing"))._base()
    assert "--no-plugin-dirs" in base and "--plugin-dirs" not in base


def test_default_dirs():
    assert Settings().ytdlp_plugin_dirs == "/opt/ytdlp-plugins,/data/ytdlp-plugins"


def test_parse_plugins():
    out = "\n".join([
        "[debug] Plugin directories: /opt/ytdlp-plugins/tastebud/yt_dlp_plugins",
        "[debug] Extractor Plugins: tastebud (VimeoIE), TastebudDemoIE",
        "[debug] Post-Processor Plugins: MyPP",
    ])
    assert parse_plugins(out) == ["tastebud (VimeoIE)", "TastebudDemoIE", "MyPP"]
    assert parse_plugins("[debug] Plugin directories: none") == []


def test_bundled_plugin_folder_has_yt_dlp_layout():
    root = Path(__file__).resolve().parents[1] / "ytdlp-plugins"
    if not root.is_dir():  # the test image copies only tests/ and vsimporter/
        root = Path("/opt/ytdlp-plugins")
    if not root.is_dir():
        pytest.skip("plugin folder not available")
    assert (root / "tastebud" / "yt_dlp_plugins" / "extractor").is_dir()
    assert not list(root.rglob("__init__.py")), "yt-dlp plugin packages must not have __init__.py"


@needs_ytdlp
async def test_real_ytdlp_loads_and_uses_a_plugin(tmp_path):
    y = RealYtDlp(settings_with(tmp_path, make_plugin(tmp_path / "plugins")))
    assert "TastebudDemoIE" in await y.plugins()
    info = await y.probe("tastebud-demo:abc")
    assert info["title"] == "Demo abc"
    assert info["extractor_key"] == "TastebudDemo"


@needs_ytdlp
async def test_real_ytdlp_override_replaces_builtin_extractor(tmp_path):
    code = "from yt_dlp.extractor.vimeo import VimeoIE\n\nclass VimeoFixIE(VimeoIE, plugin_name='tastebud'):\n    pass\n"
    y = RealYtDlp(settings_with(tmp_path, make_plugin(tmp_path / "plugins", "vimeo.py", code)))
    assert "tastebud (VimeoIE)" in await y.plugins()


@needs_ytdlp
async def test_broken_plugin_does_not_break_ytdlp(tmp_path):
    root = make_plugin(tmp_path / "plugins")
    make_plugin(root, "broken.py", "this is not python\n")
    y = RealYtDlp(settings_with(tmp_path, root))
    info = await y.probe("tastebud-demo:xyz")
    assert info["title"] == "Demo xyz"


def test_health_lists_plugins(svc):
    from fastapi.testclient import TestClient

    from vsimporter.api import create_app

    with TestClient(create_app(svc, background=False)) as client:
        assert client.get("/api/health").json()["yt_dlp_plugins"] == []
