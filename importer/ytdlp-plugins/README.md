# yt-dlp plugins

Custom or fixed site extractors for yt-dlp. Everything in this folder is copied into the Importer
image at `/opt/ytdlp-plugins` and loaded on every yt-dlp call. yt-dlp itself is not patched, so the
plugins survive `YTDLP_AUTO_UPDATE`.

## Layout

yt-dlp only finds plugins in this exact structure (no `__init__.py` files anywhere):

```
ytdlp-plugins/
  tastebud/                     # the "package" name, shown in yt-dlp's output
    yt_dlp_plugins/
      extractor/
        somesite.py             # one or more extractor plugins
      postprocessor/            # optional
```

## Fixing a site that yt-dlp already supports

Subclass yt-dlp's extractor and give the class a `plugin_name`. yt-dlp then uses your class in place
of the original for every URL it matched before. Override only what is broken; everything else is
inherited, so upstream fixes to the rest of the extractor still reach you.

```python
# tastebud/yt_dlp_plugins/extractor/somesite.py
from yt_dlp.extractor.somesite import SomeSiteIE


class SomeSiteFixIE(SomeSiteIE, plugin_name='tastebud'):
    def _real_extract(self, url):
        info = super()._real_extract(url)
        # ...adjust or replace what the original gets wrong
        return info
```

To start from a full copy instead, copy the original file from
`yt_dlp/extractor/<site>.py` in the yt-dlp repository, keep the class name, change its base to the
original class as above, and edit it. Import helpers from `yt_dlp.utils`, not with relative imports.

## A site yt-dlp does not support

Write a normal extractor (a subclass of `yt_dlp.extractor.common.InfoExtractor` with `_VALID_URL`
and `_real_extract`). It is added next to the built-in ones.

## Without rebuilding the image

The Importer also loads plugins from `/data/ytdlp-plugins` (on Unraid:
`appdata/tastebud/importer/ytdlp-plugins`), with the same layout. Restart the Importer after
changing a file there. Set `YTDLP_PLUGIN_DIRS` to use other folders (comma separated).

## Checking that a plugin loads

Admin > Import lists the loaded plugins, for example `yt-dlp plugins: tastebud (SomeSiteIE)`.
In the container:

```sh
docker exec tastebud-importer yt-dlp --verbose --plugin-dirs /opt/ytdlp-plugins --plugin-dirs /data/ytdlp-plugins --simulate URL
```

The `[debug] Extractor Plugins:` line names each plugin, and the extractor in the
`[SomeSite] ...` lines shows which one handled the URL. A plugin with a syntax error is skipped with
a warning in this output.

Extractor internals change between yt-dlp releases. If a site breaks after a yt-dlp update, check
the plugin first. If your fix would help everyone, consider sending it to yt-dlp upstream, then
delete the plugin once a release includes it.
