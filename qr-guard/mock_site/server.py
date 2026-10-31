"""가짜 테스트 페이지 서버 (Flask).

실행:  python mock_site/server.py   →  http://127.0.0.1:5001

안전 규칙
  - 127.0.0.1 에서만 열린다 (외부 접속 불가).
  - 피싱 페이지의 form action 은 "#" 이고, 입력값을 저장·전송하는 코드가 없다.
  - APK 시나리오는 실제 파일 없이 Content-Type 헤더만 돌려준다.

리다이렉트가 있는 시나리오는 302 응답의 Location 에 "표시용 URL"
(예: https://order.minttable.xyz/t/7) 을 넣는다. 검사기는 시나리오의
override 표를 보고 그 주소를 다시 이 서버의 로컬 경로로 바꿔 접속한다.
"""

from pathlib import Path

from flask import Flask, Response, abort, redirect, send_from_directory

HOST = "127.0.0.1"
PORT = 5001
PAGES = Path(__file__).resolve().parent / "pages"

app = Flask(__name__)


def page(filename: str):
    """pages/ 폴더의 HTML 파일을 돌려준다."""
    if not (PAGES / filename).exists():
        abort(404)
    return send_from_directory(PAGES, filename, mimetype="text/html")


@app.route("/")
def index():
    return page("index.html")


@app.route("/health")
def health():
    """평가 스크립트가 서버가 켜져 있는지 확인할 때 쓴다."""
    return {"status": "ok"}


# ── N1: 새솔시 공영주차장 정상 결제 (로그인 + 차량번호) ──
@app.route("/n1/pay")
def n1_pay():
    return page("n1_pay.html")


# ── N2: 단축 URL → 테이블 주문 페이지 ──
@app.route("/n2/short")
def n2_short():
    return redirect("https://order.minttable.xyz/t/7", code=302)


@app.route("/n2/t/7")
def n2_order():
    return page("n2_order.html")


# ── P1: 주차요금 결제를 가장한 카드정보·주민번호 수집 ──
@app.route("/p1/pay")
def p1_pay():
    return page("p1_pay.html")


# ── P2: 택배 재배송 → APK 설치 유도 ──
@app.route("/p2/app")
def p2_app():
    return page("p2_app.html")


@app.route("/p2/install")
def p2_install():
    # 실제 파일은 없다. 헤더만 APK 처럼 보이게 한다.
    return Response(b"", mimetype="application/vnd.android.package-archive",
                    headers={"Content-Disposition": 'attachment; filename="delivery_check.apk"'})


# ── P3: 카카오페이 사칭 로그인 + OTP ──
@app.route("/p3/login")
def p3_login():
    return page("p3_login.html")


# ── P4: 아파트 관리비 미납 → 계좌 비밀번호 요구 ──
@app.route("/p4/bill")
def p4_bill():
    return page("p4_bill.html")


# ── A1: 단축 URL → 쇼핑몰 로그인 (안내 문구 없음) ──
@app.route("/a1/short")
def a1_short():
    return redirect("https://shop-daily.co.kr/login", code=302)


@app.route("/a1/login")
def a1_login():
    return page("a1_login.html")


# ── A2: 카페 Wi-Fi 연결 (휴대폰 번호 + 마케팅 동의) ──
@app.route("/a2/join")
def a2_join():
    return page("a2_join.html")


if __name__ == "__main__":
    print(f"가짜 테스트 페이지 서버: http://{HOST}:{PORT}  (Ctrl+C 로 종료)")
    app.run(host=HOST, port=PORT, debug=False)
