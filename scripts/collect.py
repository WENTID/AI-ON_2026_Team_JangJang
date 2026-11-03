"""검증용 데이터 수집 스크립트 (요구사항 4-5).

피싱 사이트는 몇 시간~며칠이면 사라지므로, 수집 시점에 분석에 필요한 정보(리다이렉트, 헤더, HTML,
도메인 등록일, 인증서, Safe Browsing 결과)를 모두 저장해 둔다. 저장본으로 검증하면 언제 돌려도 같은 결과가 나온다.

- 피싱 URL: OpenPhish 무료 피드 (비상업적 이용 가능, 12시간마다 갱신)
- 정상 URL: config/benign_extra.txt (국내 주요 사이트·로그인 페이지) + Tranco 상위 목록
- 이미 저장한 URL과 실패한 URL은 다시 수집하지 않는다. 여러 번 실행하면 데이터가 쌓인다.
- 접속은 실시간 분석과 같은 안전 규칙(파일 다운로드 금지, 시간 제한, GET 요청만)을 따른다.

사용법:  python scripts/collect.py --phish 100 --benign 100
"""
import argparse
import gzip
import hashlib
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, lookups  # noqa: E402
from app.snapshot import collect  # noqa: E402

SNAP_DIR = config.DATA_DIR / "snapshots"
FAIL_LOG = SNAP_DIR / "failures.jsonl"


def url_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def snapshot_path(label: str, url: str) -> Path:
    return SNAP_DIR / label / f"{url_id(url)}.json.gz"


def already_done() -> set[str]:
    done = {p.name.split(".")[0] for p in SNAP_DIR.glob("*/*.json.gz")}
    if FAIL_LOG.exists():
        for line in FAIL_LOG.read_text(encoding="utf-8").splitlines():
            done.add(json.loads(line)["id"])
    return done


def phish_candidates() -> list[str]:
    urls = lookups.download_feed()
    # 수집 시점의 피드를 날짜별로 따로 보관 (출처 기록용)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (config.DATA_DIR / "feeds" / f"openphish-{stamp}.txt").write_text("\n".join(urls), encoding="utf-8")
    random.Random(0).shuffle(urls)
    return urls


def benign_candidates() -> list[str]:
    extra = config._load_lines("benign_extra.txt")
    tranco = config._load_lines("whitelist_tranco.txt")[:1000]
    random.Random(0).shuffle(tranco)
    return extra + [f"https://{d}/" for d in tranco]


def is_usable(snap: dict) -> str | None:
    """검증에 쓸 수 없는 결과면 사유를 돌려준다."""
    trace = snap["trace"]
    if trace["error"]:
        return trace["error"]
    if trace["status"] is None or trace["status"] >= 400:
        return f"HTTP {trace['status']}"
    if not snap["html"] and not trace["is_download"]:
        return "HTML 없음"
    return None


def collect_one(url: str, label: str, source: str) -> tuple[str, str, str | None]:
    snap = collect(url, source=source, label=label)
    reason = is_usable(snap)
    if reason:
        return url, label, reason
    path = snapshot_path(label, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    return url, label, None


def run(label: str, source: str, candidates: list[str], want: int, workers: int, done: set[str]) -> None:
    todo = [u for u in dict.fromkeys(candidates) if url_id(u) not in done]
    saved = failed = 0
    print(f"[{label}] 후보 {len(todo)}개 중 {want}개 저장 목표")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        it = iter(todo)
        running = {}
        # 성공 개수가 목표에 닿을 때까지 후보를 조금씩 넣는다. (실패가 많은 피싱 URL 대비)
        def submit_next():
            u = next(it, None)
            if u is not None:
                running[pool.submit(collect_one, u, label, source)] = u
        for _ in range(min(workers, want)):
            submit_next()
        while running:
            fut = next(as_completed(running))
            running.pop(fut)
            url, _, reason = fut.result()
            done.add(url_id(url))
            if reason:
                failed += 1
                SNAP_DIR.mkdir(parents=True, exist_ok=True)
                with FAIL_LOG.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"id": url_id(url), "url": url, "label": label, "reason": reason,
                                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                                       ensure_ascii=False) + "\n")
            else:
                saved += 1
                print(f"  저장 {saved}/{want}: {url[:90]}")
            if saved + len(running) < want:
                submit_next()
    print(f"[{label}] 저장 {saved}개, 실패 {failed}개 (실패 사유: {FAIL_LOG})")


def fix_domains(workers: int) -> None:
    """저장본 중 도메인 등록일 조회에 실패한 것만 다시 조회해 채운다. (등록일은 바뀌지 않으므로 나중에 채워도 됨)"""
    targets = []
    for path in SNAP_DIR.glob("*/*.json.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            snap = json.load(f)
        if not snap["domain"]["checked"] and snap["domain"]["error"] != "IP 주소라 조회 대상 아님":
            targets.append((path, snap))

    def work(item):
        path, snap = item
        snap["domain"] = lookups.lookup_domain(snap["domain"]["domain"])
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        return snap["domain"]["checked"]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        ok = sum(pool.map(work, targets))
    print(f"도메인 정보 재조회: {len(targets)}개 중 {ok}개 성공")


def main() -> None:
    parser = argparse.ArgumentParser(description="검증용 피싱/정상 URL 스냅샷 수집")
    parser.add_argument("--phish", type=int, default=100, help="이번 실행에서 새로 저장할 피싱 URL 수")
    parser.add_argument("--benign", type=int, default=100, help="이번 실행에서 새로 저장할 정상 URL 수")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--fix-domains", action="store_true", help="저장본의 비어 있는 도메인 등록일만 다시 조회")
    args = parser.parse_args()

    if args.fix_domains:
        fix_domains(min(args.workers, 4))
        return

    done = already_done()
    if args.phish:
        run("phishing", "openphish", phish_candidates(), args.phish, args.workers, done)
    if args.benign:
        run("benign", "tranco+benign_extra", benign_candidates(), args.benign, args.workers, done)

    counts = {d.name: len(list(d.glob("*.json.gz"))) for d in SNAP_DIR.iterdir() if d.is_dir()}
    print("누적 저장본:", counts)


if __name__ == "__main__":
    main()
