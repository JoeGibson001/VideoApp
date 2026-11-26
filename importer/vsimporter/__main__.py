"""Entry point: python -m vsimporter"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from .api import create_app
from .config import Settings
from .logs import setup_logging
from .proxy import masked
from .service import Importer


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    log = logging.getLogger("vsimporter")
    if settings.proxy:
        firewall = os.environ.get("TASTEBUD_FIREWALL") == "active"
        if settings.proxy_kill_switch and not firewall:
            # Fail closed: the entrypoint sets the firewall; without it a stray request could go direct.
            log.error("PROXY_URL is set but the firewall is not active (start through the container "
                      "entrypoint, or set PROXY_KILL_SWITCH=false); refusing to start")
            sys.exit(1)
        log.info("all internet traffic goes through the proxy",
                 extra={"proxy": masked(settings.proxy), "firewall": firewall})
    app = create_app(Importer(settings))
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None, proxy_headers=True)


if __name__ == "__main__":
    main()
