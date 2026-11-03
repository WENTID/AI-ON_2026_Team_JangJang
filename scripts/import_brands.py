"""팀에서 조사한 브랜드 목록(config/brands_team.csv)을 분석기가 쓰는 config/brands.json 으로 바꾼다.

CSV 열: brand_id, name_ko, name_en, region, category, official_domains(공백 구분), phishing_urls
- 별칭(aliases): 영문명, 괄호·슬래시로 나뉜 이름 + 기존 brands.json 에 있던 별칭(카카오톡, 홈택스 등)을 합친다.
- 유사 도메인 탐지용 키워드(keywords): 공식 도메인의 이름 부분 중 5글자 이상이고 흔한 단어가 아닌 것.
- 누구나 페이지를 올릴 수 있는 주소(HOSTED)는 공식 도메인에서 뺀다. 넣으면 그곳의 피싱 페이지를 신뢰하게 된다.
사용법:  python scripts/import_brands.py
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.urls import domain_label  # noqa: E402

CSV_PATH = ROOT / "config" / "brands_team.csv"
OUT_PATH = ROOT / "config" / "brands.json"

# 공식 도메인에서 뺄 주소: 사용자가 만든 페이지가 올라가는 곳 (settings.json 의 hosting_platforms 에도 있음)
HOSTED = {"googleusercontent.com", "sharepoint.com"}
# 유사 도메인 키워드로 쓰지 않을 단어: 흔한 영어 단어이거나, 한 글자만 바꾸면 흔한 단어가 되는 것
COMMON_WORDS = {"office", "outlook", "live", "booking", "chase", "steam", "auction", "apple", "police", "tworld",
                "scourt", "efine", "visa", "line", "uplus", "imbank", "truefriend"} | {domain_label(d) for d in HOSTED}
# 이름을 나누다 생기는 일반 단어는 별칭으로 쓰지 않는다. ('우체국(택배)' → '택배', '카카오/다음' → '다음')
COMMON_ALIASES = {"택배", "다음", "은행", "카드", "증권"}


def split_names(name: str) -> list[str]:
    """'iM뱅크(대구은행)' → ['iM뱅크(대구은행)', 'iM뱅크', '대구은행'], '카카오/다음' → [..., '카카오', '다음']"""
    parts = [name] + [p.strip() for p in re.split(r"[()/]", name) if p.strip()]
    return list(dict.fromkeys(parts))


def main() -> None:
    old = json.loads(OUT_PATH.read_text(encoding="utf-8"))["brands"] if OUT_PATH.exists() else []
    brands = []
    with CSV_PATH.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            domains = [d.lower() for d in row["official_domains"].split() if d.lower() not in HOSTED]
            names = split_names(row["name_ko"].strip())
            aliases = names[1:] + split_names(row["name_en"].strip())
            keywords = []
            for d in domains:
                label = domain_label(d)
                if len(label) >= 5 and label not in COMMON_WORDS and not label.isdigit():
                    keywords.append(label)
            # 기존 목록에서 대표 도메인이 겹치는 브랜드의 별칭·키워드를 이어받는다.
            for prev in old:
                if prev["official_domains"][0] in domains:
                    aliases += [prev["name"], *prev.get("aliases", [])]
                    keywords += [k for k in prev.get("keywords", []) if k not in COMMON_WORDS]
            aliases = [a for a in dict.fromkeys(aliases) if a and a != names[0] and a not in COMMON_ALIASES]
            brands.append({
                "id": row["brand_id"],
                "name": names[0],
                "aliases": aliases,
                "category": row["category"],
                "region": row["region"],
                "official_domains": domains,
                "keywords": list(dict.fromkeys(keywords)),
            })

    OUT_PATH.write_text(json.dumps({
        "_설명": "scripts/import_brands.py 가 config/brands_team.csv(팀 조사 목록)에서 만든 파일. 직접 고치지 말고 CSV를 고친 뒤 스크립트를 다시 실행한다.",
        "brands": brands,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{OUT_PATH} 에 브랜드 {len(brands)}개 저장")


if __name__ == "__main__":
    main()
