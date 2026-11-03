"""Tranco 상위 사이트 목록을 받아 config/whitelist_tranco.txt 를 만든다.

Tranco(https://tranco-list.eu): 연구용으로 만든 신뢰할 수 있는 인기 사이트 순위. 무료로 공개되어 있다.
사용법:  python scripts/update_whitelist.py --top 10000
"""
import argparse
import io
import sys
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402

TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"


def download_tranco() -> list[str]:
    cache = config.DATA_DIR / "feeds" / "tranco-top-1m.csv.zip"
    if not cache.exists():
        print("Tranco 목록 다운로드 중...")
        r = httpx.get(TRANCO_URL, timeout=120, follow_redirects=True)
        r.raise_for_status()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(r.content)
    with zipfile.ZipFile(cache) as z:
        text = z.read(z.namelist()[0]).decode("utf-8")
    return [line.split(",", 1)[1].strip().lower() for line in io.StringIO(text) if "," in line]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=10000, help="상위 몇 개를 신뢰 목록에 넣을지")
    args = parser.parse_args()

    domains = download_tranco()[: args.top]
    out = config.CONFIG_DIR / "whitelist_tranco.txt"
    out.write_text("# Tranco 상위 목록 (scripts/update_whitelist.py 로 생성)\n" + "\n".join(domains) + "\n", encoding="utf-8")
    print(f"{out} 에 {len(domains)}개 저장")


if __name__ == "__main__":
    main()
