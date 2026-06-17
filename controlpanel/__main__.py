"""Entry point: ``python -m controlpanel``.

Host/port come from PANEL_HOST (default 0.0.0.0) and PANEL_PORT (default 8800).
"""
from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    host = os.environ.get("PANEL_HOST", "0.0.0.0")
    port = int(os.environ.get("PANEL_PORT", "8800"))
    uvicorn.run("controlpanel.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
