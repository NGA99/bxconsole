# BXConsole 용어 규격서 (TERMS)

이 문서는 BXConsole의 컴포넌트·UI·기능 용어를 정의한다. 코드/문서/대화에서 반드시 이 명칭을 사용한다.
(기준: 2026-10-16 코드 상태, v0.3)

---

## 1. 시스템 구성 (Components)

| 용어 | 정의 | 실체 |
|---|---|---|
| **C2 서버** (C2) | 비컨 수집·명령 큐잉·세션 상태 관리 서버. 표준 라이브러리만 사용 | `c2/server.py`, 기본 `:7700`, `0.0.0.0` 바인딩 |
| **운영자 콘솔** (Operator Console) | 운영자가 사용하는 웹 UI 전체. `/bxconsole`로 서빙 (구 `/op` → 302) | `c2/op.html` |
| **운영자 CLI** | 터미널 REPL. 웹 콘솔과 동일 API 사용 | `c2/cli.py` |
| **페이로드** (payload) | 피해자 페이지에서 실행되는 스크립트. 비컨·채널·훅·자동수집 포함 | `payload/bx.js`, URL `{C2}/bx.js` |
| **피해자** (victim) | 페이로드가 실행된 **브라우저 탭**. 식별자는 vid | — |
| **세션** (session) | 하나의 vid에 묶인 연결 단위. 상태/수집물/명령 이력의 컨테이너 | `sessions/{vid}/` |
| **실습 대상** (lab target) | 저장형 싱크가 있는 테스트 사이트 (개발·QA 전용) | `lab/target.py`, `:8000` |

⚠️ **"피해자"와 "세션"은 동일시하지 않는다.** 피해자 = 브라우저 탭(vid 보유), 세션 = 그 탭의 연결 상태 + 기록물.
탭 새로고침 → 동일 피해자, 동일 vid, 새 bn(아래 참조).

---

## 2. 식별자

| 용어 | 정의 | 저장 위치 |
|---|---|---|
| **vid** (victim id) | 세션 식별자. `sessionStorage.bx_id` — 탭당 1개, 브라우저 종료 시 유실 | `sessions/{vid}/` |
| **bn** (boot number) | 페이지 로드 인스턴스 번호. 새로고침 판별·SENT 재큐 트리거에 사용 | `sessionStorage` 아님, 매 로드 랜덤 |
| **cid** (command id) | 명령 식별자. `HHMMSS+랜덤3` | `commands.jsonl` |
| **tag** | 비컨 종류 또는 probe 라벨 | — |

**비컨 종류 (beacon tags)**: `info` `mode` `beat` `ack` `result` `net` `console` `html` `screenshot` `proxy` `pageinfo`
(단, `beat`는 evidence 원장 미기록)

---

## 3. 세션 상태 (Session Lifecycle)

```
연결(첫 비컨) → Active ──Delete──▶ Trashed ──Purge──▶ Purged
                 │  ▲                  (복원 가능)        (영구 삭제)
                 └ Close┘  Closed ←→ Active (새 bn 재접속 시 부활)
```

| 용어 | 정의 | UI 표시 |
|---|---|---|
| **Active** | 살아있는 세션. 상태 점으로 세분화 | — |
| **Closed** | 운영자/CLI가 명시적으로 종료. 같은 bn 비컨 무시, 새 bn → 부활. 내용물 보존 | ⚪ 회색 + "Closed" |
| **Trashed** (soft delete) | `sessions/{vid}/` → `.trash/` 이동. 웹에서 숨김, 같은 vid 재비컨 무시 | Sessions 탭 TRASH 영역 |
| **Purged** (hard delete) | 디렉터리 영구 삭제 + `.purged.json` 등록 = **재등록 영구 차단** | 목록에서 소멸 |

### 상태 점 (Status Dot) — Active 세션의 실시간 상태

| 상태 | 조건 | 색 |
|---|---|---|
| **live** | 마지막 비컨 ≤ 5초 | 🟢 초록 |
| **stale** | ≤ 35초 (비컨 1회 지연, 복구 대기) 또는 SSE 연결 중 | 🟡 노랑 |
| **red** | > 35초 무응답 **&& SSE 없음** = 완전 끊김 | 🔴 빨강 |
| **dead** | Closed 세션 | ⚪ 회색 |

---

## 4. 채널과 명령 (Channels & Commands)

| 용어 | 정의 |
|---|---|
| **비컨** (beacon) | 피해자 → C2 단방향 보고. `POST /b/<tag>` (sendBeacon, 실패 시 Image 폴백) |
| **SSE 채널** | `GET /sse?id=` — 명령 푸시 채널. 끊기면 브라우저가 자동 재연결, 재연결 시 SENT 명령 재큐 |
| **폴링 채널** (polling) | `GET /cmd?id=` — SSE 폴백. 4초 주기 |
| **명령** (command) | 운영자 → 피해자로 보내는 JS 코드. 큐잉 → 전달 → 피해자 실행 → 결과 비컨 |
| **명령 상태 전이** | `QUEUED → SENT → RECV(ack 수신) → DONE`. 전송 실패/무응답 시 **재큐**(새 SSE 연결·bn 변경·30초 stale watcher) |
| **stale watcher** | 30초 이상 SENT로 고정된 명령을 자동 재큐하는 서버 스레드 (15초 주기 감시) |
| **중복 제거** (dedup) | 이중 채널(SSE+폴링)로 같은 명령이 중복 전달돼도 피해자의 `bx_seen_{cid}`로 1회만 실행 |

### 슬래시 커맨드 (Slash Commands) — Console 입력창 전용

| 커맨드 | 기능 |
|---|---|
| `/screenshot` | 즉시 캡처 → 명령 바로 아래 썸네일(클릭 확대) |
| `/html` | 즉시 덤프 → 명령 바로 아래 링크(새 탭 페이지 뷰) |

⚠️ **Proxy는 슬래시 커맨드가 아니다** — Network 탭 툴바의 "🔁 Proxy Request" 버튼/모달, 또는
Console에서 `await bx.proxy('/url')` 직접 입력.

---

## 5. 운영자 콘솔 UI 구조

```
┌ 세션바 (session bar) ── 피해자 세션 탭들 [상태 점·이름·✎·✕] ← 드래그 순서 조정 가능
├ 패널 탭 (panel tabs) ── Console · Network · Sessions · Payloads · Probe · Settings ← 드래그 순서 조정 가능
│   ├ Console 패널 — 콘솔 스트림 + 입력창
│   ├ Network 패널 — 네트워크 캡처 테이블
│   ├ Sessions 패널 — 세션 목록 + TRASH (⚠ "세션 패널" 금지 — 세션 탭과 혼동)
│   ├ Payloads 패널 — 페이로드 생성기
│   ├ Probe 패널 — probe hit 실시간 목록
│   └ Settings 패널 — 자동수집 플래그
└ 상태 pill (우측) — 연결 상태 · 현재 URL
```

### 탭의 두 종류 — "누구 데이터를 볼지" vs "무엇을 볼지"

| 용어 | 정의 |
|---|---|
| **세션 탭** (session tab) | 세션바의 피해자별 탭. **"누구 데이터를 볼지"** 선택. 클릭 = active 전환 → 모든 패널이 그 세션 데이터 표시 |
| **패널 탭** (panel tab) | 기능 선택 탭. **"무엇을 볼지"** 선택. 클릭 = 같은 active, 다른 기능 뷰 |
| **패널** (panel) | 탭 아래의 내용 영역. 피해자 전용 패널은 없음 — 6개 패널을 모든 세션이 공유 |

⚠️ **"세션 탭" vs "Sessions 패널"**: 세션 탭은 피해자 하나(상단바), Sessions 패널은 전체 세션 목록+휴지통(패널).
"세션 패널"이라는 말은 쓰지 않는다 — 세션 탭과 혼동된다.

⚠️ **"피해자가 들어오면 탭이 열린다"의 정확한 의미:**
1. 세션바에 **세션 탭이 추가**됨 (NEW 마크)
2. **자동 포커스(auto-select)** — 다른 active가 없으면 Console 패널로 자동 전환 + 그 세션 선택
3. Sessions 패널 탭의 배지가 플래시 (새 피해자 알림)

즉 새 피해자에게 **열리는 화면은 Console 패널**, 생기는 것은 **세션 탭**이다.
피해자별 화면을 분리하고 싶으면 세션 탭을 클릭해 active를 전환하면 되고, 패널 탭으로 기능을 바꾸면 된다.

| 용어 | 정의 |
|---|---|
| **자동 포커스** (auto-select) | 피해자 연결 시 active가 없으면 Console 패널로 전환 + 해당 세션 선택하는 동작 |
| **NEW 마크** | 새 피해자 세션 탭에 표시되는 배지 (다음 폴링까지 유지) |
| **세션바** (session bar) | 최상단 가로 탭들 = 피해자 세션 목록. ✕는 **콘솔에서만 숨김**(세션 생존), Sessions 패널 클릭으로 복원 |
| **패널 탭** (panel tab) | DevTools 스타일 6개 탭. 드래그 순서 조정 · localStorage 위치 고정 |
| **콘솔 스트림** (console stream) | Console 패널의 단일 시간순 출력물 = auto-collect 그룹 + 명령/결과 + victim 콘솔 + 슬래시 결과 |
| **명령 히스토리** | 스트림 내 명령행(`› 코드` + 상태 배지) — **결과는 항상 명령 바로 아래** |
| **victim 콘솔 로그** | 피해자의 `console.*`/onerror 캡처 (`console` 비컨) — 콘솔 스트림에 시각순 삽입 |
| **auto-collect 그룹** | 스트림 최상단(연결 시각)에 붙는 자동수집 요약(pageinfo) + **수집 아티팩트(스크린샷 썸네일·덤프 링크)** |
| **로컬 echo** (queued echo) | 입력 즉시 표시되는 `› 명령 [Queued]` 행 — 다음 폴링 때 서버 상태로 교체 |
| **Proxy 모달** | 피해자 세션으로 대행 요청하는 모달 (`bx.proxy`) — 툴바 버튼 또는 행 상세에서 진입 |
| **라이트박스** (lightbox) | 스크린샷 썸네일 클릭 시 전체화면 확대 |

---

## 6. 자동수집과 수집물 (Auto-collect & Artifacts)

| 용어 | 정의 | 파일 |
|---|---|---|
| **자동수집** (auto-collect) | 세션 연결 시 1회 자동 실행: pageinfo + HTML 덤프 + 스크린샷 (Settings 플래그로 on/off, 피해자 페이지 로드 시 1회 읽음) | — |
| **pageinfo** | 브라우저/페이지 메타 (화면·언어·리퍼러·폼·csrf 등) | `pageinfo.json` |
| **HTML 덤프** (HTML dump) | 피해자 실제 문서(`bx.doc()`)의 DOM 스냅샷. 청크 비컨 조립 | `htmldump.html` |
| **스크린샷** (screenshot) | 피해자 실제 뷰포트 캡처. html2canvas를 피해자 문서에 주입해 캡처 (scale 1) | `screenshot.jpg` |
| **수집물** (artifact) | 위 수집물의 총칭. 콘솔 스트림 auto-collect 그룹에 자동 표시(썸네일+링크) | — |
| **bx.doc()** | anchor 모드에서 `frames[0].document`(동일 출처)를 해석 — 껍데기가 아닌 실제 문서 | — |
| **bx.shot()** | 스크린샷 캡처 실행 (probe 태그 자동 중복 방지) | — |
| **bx.htmldump()** | HTML 덤프 실행 | — |

⚠️ **anchor 모드**: top 프레임은 껍데기(iframe 1개), 실제 피해자 문서는 `frames[0]`.
모든 수집·캡처는 `bx.doc()` 기준 — 껍데기가 아닌 피해자가 보는 화면.
**hijack 모드**: XFO DENY 페이지 폴백. soft-nav로 실제 DOM이 top에 유지됨.

---

## 7. Probe (연결성 확인)

| 용어 | 정의 |
|---|---|
| **probe** | 본 페이로드 없이 **연결성·필터 통과 여부**만 확인하는 요청. JS 불필요 (리소스 로드) |
| **probe hit** | probe 요청 1건의 기록 (시각·IP·UA·**Referer=발생 페이지**·Sec-Fetch-*) — Probe 패널에 실시간 표시 |
| **one-shot** | img+script+css+fetch probe 4종을 **한 번에 발사하는 묶음 페이로드** — 어느 채널이 통과하는지 한 방에 진단 |
| **probe 벡터** | `img`(img-src) · `script`(script-src) · `css`(style-src) · `fetch`(connect-src) |
| **응답 형식** | `/probe` → 1x1 GIF, `/probe.js` → 유효 JS, `/probe.css` → 빈 CSS (브라우저 에러 방지) |
| **/bx.js 로드 기록** | `/bx.js` 요청 자체도 hit으로 기록 — Referer로 어떤 페이지가 페이로드를 로드했는지 추적 |

---

## 8. 페이로드 종류 (Payloads 패널)

| 용어 | 정의 |
|---|---|
| **Console trigger** | 피해자 DevTools 콘솔에 붙여넣는 JS 스니펫 (`import()` / fetch+eval / createElement). HTML 아님 |
| **injection vector** | 주입 형태: script 태그 / attribute breakout(`">`) / tag handler(svg·body·input 등) / encoding 우회 |
| **C2 base** | 페이로드에 삽입될 C2 주소. 기본 = /bxconsole 페이지 origin, 수동 오버라이드 시 localStorage 영속 |
| **스킴 자동 감지** | `/bx.js`가 Host 헤더 + TLS/X-Forwarded-Proto로 http/https를 자동 선택 (mixed content 방지) |
| **비컨 URL 자기 적응** | 피해자가 도달한 주소(호스트+스킴)가 곧 비컨 주소 — 서버 이동에 코드 수정 불필요 |

---

## 9. 엔드포인트 목록

### 운영자
| 엔드포인트 | 기능 |
|---|---|
| `GET /bxconsole` | 운영자 콘솔 (구 `/op` → 302) |
| `GET /api/state` | 전체 상태 (세션·명령·콘솔·net·pageinfo·trash·probe 카운트) |
| `POST /api/send` | 명령 큐잉 `{id, cmd}` |
| `POST /api/close` · `/api/reopen` | 세션 종료/재개 |
| `POST /api/delete` · `/api/restore` · `/api/purge` | 휴지통 이동 / 복원 / 완전파기 |
| `POST /api/rename` | 세션 표시명 변경 |
| `GET/POST /api/config` | 자동수집 플래그 |
| `GET /api/net?id` · `POST /api/net/clear` | 네트워크 캡처 조회/클리어 |
| `GET /api/screenshot?id` · `GET /api/htmldump?id` · `GET /shot/{vid}` | 수집물 조회 (JSON / HTML / JPEG) |
| `GET /api/probes` · `POST /api/probes/clear` | probe hit 목록/클리어 |
| `POST /api/send` 외 | proxy/ack/result 등은 비컨 경유 |

### 피해자 (페이로드)
| 엔드포인트 | 기능 |
|---|---|
| `GET /bx.js` | 페이로드 서빙 (C2 치환 + hit 기록 + no-store) |
| `GET /sse?id` · `GET /cmd?id` | 명령 채널 (SSE/폴링) |
| `POST /b/<tag>` | 비컨 수집 |
| `GET /config` | 자동수집 플래그 (no-store) |
| `GET /probe` · `/probe.js` · `/probe.css` | 연결성 확인 (hit 기록) |
| `GET /shot/{vid}` | 수집 스크린샷 JPEG |

---

## 10. 저장소 (Storage)

| 용어 | 정의 |
|---|---|
| **세션 워크스페이스** | `sessions/{vid}/` — meta·commands·console·net·pageinfo·htmldump·screenshot. 재시작 시 자동 복원 |
| **휴지통** | `sessions/.trash/{vid}/` — soft delete 보관소 |
| **파기 인덱스** | `sessions/.purged.json` — 파기된 vid 목록 (고스트 재등록 영구 차단) |
| **evidence 원장** (ledger) | `evidence/sessions-YYYYMMDD.jsonl` — append-only. beat·/bx.js 로드 제외 전 기록 + 삭제/파기 이벤트 |
| **설정** | `c2/config.json` (자동수집 플래그·알림), `c2/names.json` (표시명) — 둘 다 gitignore |

---

## 11. 통일 이전 용어 → 표준 용어

| 이전에 섞어 쓴 말 | 표준 |
|---|---|
| "콘솔" (모호) | 문맥별: **운영자 콘솔**(/bxconsole) / **Console 패널** / **콘솔 스트림** / **victim 콘솔 로그** / **Console trigger** |
| "탭" (모호) | **세션바 탭**(피해자) / **패널 탭**(기능) |
| "원샷" | probe 묶음 = **one-shot**. 단일 실행 페이로드는 그냥 페이로드 |
| "리퀘스트빈" | **probe** / **probe hit** |
| "덤프" | **HTML 덤프** (htmldump) |
| "삭제" | Delete(**Trashed**) vs Purge(**파기**) 구분 |
| "새로고침 부활" | **부활**(revival) — 새 bn 비컨으로 Closed → Active |
| "피해자" | 브라우저 탭. 연결 단위는 **세션** |
