"""Entry point: python -m vsimporter"""

from __future__ import annotations

import uvicorn

from .api import create_app
from .config import Settings
from .logs import setup_logging
from .service import Importer


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    app = create_app(Importer(settings))
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None, proxy_headers=True)


if __name__ == "__main__":
    main()
