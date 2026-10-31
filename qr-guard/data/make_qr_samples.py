"""시나리오마다 예시 QR 이미지를 만든다 (시연·테스트용).

실행: python data/make_qr_samples.py   →  data/qr_samples/P1.png 등 생성
"""

import json
from pathlib import Path

import qrcode

HERE = Path(__file__).resolve().parent
OUT = HERE / "qr_samples"


def main():
    OUT.mkdir(exist_ok=True)
    scenarios = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    for s in scenarios:
        img = qrcode.make(s["qr_url"])
        path = OUT / f"{s['id']}.png"
        img.save(path)
        print(f"{path.name}: {s['qr_url']}")


if __name__ == "__main__":
    main()
