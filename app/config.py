"""설정 파일(config/*.json, 화이트리스트)과 환경변수(.env)를 읽어 오는 모듈."""
import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"

load_dotenv(ROOT / ".env")


def _load_json(name: str) -> dict:
    return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))


def _load_lines(name: str) -> list[str]:
    path = CONFIG_DIR / name
    if not path.exists():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


@lru_cache
def settings() -> dict:
    return _load_json("settings.json")


@lru_cache
def scoring() -> dict:
    return _load_json("scoring.json")


@lru_cache
def brands() -> list[dict]:
    return _load_json("brands.json")["brands"]


@lru_cache
def whitelist() -> frozenset[str]:
    """신뢰 사이트 목록 = Tranco 상위 목록 + 직접 관리하는 목록 + 브랜드 공식 도메인."""
    domains = set(_load_lines("whitelist_tranco.txt")) | set(_load_lines("whitelist_custom.txt"))
    for brand in brands():
        domains.update(d.lower() for d in brand["official_domains"])
    return frozenset(domains)


def env(name: str) -> str:
    return os.getenv(name, "").strip()
