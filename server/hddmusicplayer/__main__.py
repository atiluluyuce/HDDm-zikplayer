"""Giriş noktası: python3 -m hddmusicplayer"""

from __future__ import annotations

import uvicorn

from . import config


def main() -> None:
    uvicorn.run(
        "hddmusicplayer.web:app",
        host=config.HTTP_HOST,
        port=config.HTTP_PORT,
        log_level="info",
        access_log=False,   # Pi'de SD karta gereksiz yazma yapmasın
        workers=1,          # durum bellekte tutuluyor; tek işlem şart
    )


if __name__ == "__main__":
    main()
