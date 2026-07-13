"""AI Agent 품질관리 플랫폼 - 윈도우 데스크톱 실행 파일.

기존 웹 시스템(app/main.py, Docker 컨테이너)은 전혀 수정하지 않고, 이미 떠있는
서버에 붙어서 같은 화면을 브라우저 탭이 아닌 네이티브 윈도우 창으로 띄워주기만 하는
얇은 래퍼. 서버가 하나이므로 브라우저로 접속하든 이 프로그램으로 접속하든 데이터는
항상 동일하게 연동됨.

다른 PC에 이 exe 하나만 복사해서 배포해도 동작하도록, 접속할 서버 주소를 이 실행 파일
옆의 server_config.txt에 저장해두고 다음 실행부터 재사용한다 (없으면 기본값
http://127.0.0.1:8000, 환경변수 QA_PLATFORM_URL이 있으면 그게 최우선).
"""
import ctypes
import os
import sys
import urllib.request
import winreg
from pathlib import Path

DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
WINDOW_TITLE = "AI Agent 품질관리 플랫폼"
WEBVIEW2_DOWNLOAD_URL = (
    "https://developer.microsoft.com/microsoft-edge/webview2/"
)
# WebView2 Evergreen Runtime의 고정 Client GUID (모든 설치 방식에서 동일).
_WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _messagebox(text: str, title: str = WINDOW_TITLE, icon: int = 0x10) -> None:
    """다른 PC에서 콘솔 없이(--windowed) 실행돼도 사용자가 원인을 알 수 있도록
    윈도우 기본 메시지박스로 안내(추가 의존성 없이 ctypes만 사용)."""
    ctypes.windll.user32.MessageBoxW(0, text, title, icon)


def _webview2_installed() -> bool:
    """WebView2 Runtime(Evergreen) 설치 여부를 레지스트리로 확인.

    pywebview는 윈도우에서 기본적으로 이 런타임(Edge 기반)을 사용하는데, 최신 Windows
    10/11은 대부분 기본 포함이지만 오래되었거나 사내 이미지로 굳힌 PC에는 없을 수 있어,
    다른 PC 배포 시 "더블클릭해도 아무 반응 없음"의 가장 흔한 원인이 된다.
    """
    candidates = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_CLIENT_GUID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_CLIENT_GUID}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_CLIENT_GUID}"),
    ]
    for hive, path in candidates:
        try:
            with winreg.OpenKey(hive, path) as key:
                winreg.QueryValueEx(key, "pv")
                return True
        except OSError:
            continue
    return False


def _base_dir() -> Path:
    # PyInstaller onefile: sys.executable is the exe itself (실제 실행 위치).
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


CONFIG_PATH = _base_dir() / "server_config.txt"


def _load_saved_url() -> str | None:
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8").strip()
        return text or None
    except OSError:
        return None


def _save_url(url: str) -> None:
    # 다른 PC에서 exe를 쓰기 권한 없는 폴더(Program Files 등)에 두고 실행한 경우에도
    # 접속 자체는 계속 되도록, 설정 저장 실패는 무시한다(다음 실행 시 다시 물어보게 됨).
    try:
        CONFIG_PATH.write_text(url.strip(), encoding="utf-8")
    except OSError:
        pass


def _initial_server_url() -> str:
    return os.environ.get("QA_PLATFORM_URL") or _load_saved_url() or DEFAULT_SERVER_URL


def _server_reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/monitoring/summary", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


class Api:
    """pywebview의 js_api 브리지 - JS에서 window.pywebview.api.*로 호출.

    연결 화면은 pywebview의 html= 문자열로 로드되어 실제 HTTP origin이 없으므로,
    거기서 fetch()로 다른 origin(서버)을 호출하면 브라우저 CORS에 막힌다(서버에
    CORS 헤더를 추가할 필요는 없음 - 애초에 브라우저 fetch를 안 쓰면 됨). 그래서
    서버 도달 가능 여부 확인은 항상 이 파이썬 쪽 urllib으로 대신 수행한다.
    """

    def check_reachable(self, url: str) -> bool:
        return _server_reachable(url)

    def save_server_url(self, url: str) -> bool:
        _save_url(url)
        return True


_CONNECT_HTML_TEMPLATE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8" />
<style>
  body {
    margin: 0; height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #0f172a; color: #e2e8f0; font-family: "Segoe UI", "Malgun Gothic", sans-serif;
  }
  .box { text-align: center; max-width: 520px; padding: 2rem; }
  h1 { font-size: 1.25rem; margin-bottom: 0.5rem; }
  p { color: #94a3b8; font-size: 0.9rem; line-height: 1.6; }
  code {
    background: #1e293b; padding: 0.15rem 0.4rem; border-radius: 4px;
    font-family: Consolas, monospace; color: #38bdf8;
  }
  .spinner {
    width: 32px; height: 32px; margin: 0 auto 1.25rem;
    border: 3px solid #334155; border-top-color: #38bdf8; border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .connect-box {
    margin-top: 1.75rem; padding-top: 1.5rem; border-top: 1px solid #1e293b; text-align: left;
  }
  label { display: block; font-size: 0.8rem; color: #94a3b8; margin-bottom: 0.4rem; }
  .row { display: flex; gap: 0.5rem; }
  input {
    flex: 1; padding: 0.5rem 0.6rem; border-radius: 6px; border: 1px solid #334155;
    background: #1e293b; color: #e2e8f0; font-family: Consolas, monospace; font-size: 0.85rem;
  }
  button {
    padding: 0.5rem 1.1rem; border: none; border-radius: 6px;
    background: #1d4ed8; color: white; cursor: pointer; font-size: 0.9rem; white-space: nowrap;
  }
  button:hover { background: #1e40af; }
  button:disabled { background: #334155; cursor: not-allowed; }
  #status { display: none; margin-top: 1rem; font-size: 0.85rem; }
  #status.err { color: #f87171; display: block; }
  #status.ok { color: #4ade80; display: block; }
</style></head>
<body>
  <div class="box">
    <div class="spinner"></div>
    <h1>서버에 연결하는 중입니다...</h1>
    <p id="waiting"><code id="waitingUrl">__INITIAL_URL__</code> 서버 응답을 기다리고 있습니다.</p>

    <div class="connect-box">
      <label for="urlInput">
        접속할 서버 주소 (이 PC에서 서버를 직접 띄웠다면 기본값 그대로,
        다른 PC의 서버에 접속하려면 그 PC의 IP로 변경 - 팀원용_접속가이드.md 참고)
      </label>
      <div class="row">
        <input id="urlInput" type="text" value="__INITIAL_URL__" />
        <button id="connectBtn" onclick="connectTo()" disabled>연결</button>
      </div>
      <div id="status"></div>
    </div>
  </div>
  <script>
    // pywebview의 'pywebviewready' 이벤트에만 의존하지 않는다 - 일부 PC/WebView2 버전
    // 조합에서 이 이벤트가 늦게 오거나 안 올 수 있고, 그러면 연결 버튼이 영원히
    // 비활성 상태로 남아 "진행이 안 되는" 것처럼 보이는 문제가 생겼었다. 대신 API가
    // 실제로 쓸 수 있는지를 직접 짧은 간격으로 폴링하고, 그래도 일정 시간 안에 준비가
    // 안 되면 버튼을 강제로라도 활성화해서 항상 재시도할 방법을 남겨둔다.
    function apiAvailable() {
      return !!(window.pywebview && window.pywebview.api && window.pywebview.api.check_reachable);
    }

    function waitForApi(maxMs, cb) {
      const start = Date.now();
      (function poll() {
        if (apiAvailable() || Date.now() - start > maxMs) { cb(); return; }
        setTimeout(poll, 150);
      })();
    }

    function normalize(url) {
      url = url.trim();
      if (!/^https?:\\/\\//i.test(url)) url = 'http://' + url;
      return url.replace(/\\/+$/, '');
    }

    function tryConnect(url, save) {
      const statusEl = document.getElementById('status');
      if (!apiAvailable()) {
        statusEl.className = 'err';
        statusEl.textContent = '아직 초기화 중입니다. 잠시 후 다시 눌러주세요.';
        return Promise.reject(new Error('api not ready'));
      }
      return window.pywebview.api.check_reachable(url).then(ok => {
        if (!ok) {
          statusEl.className = 'err';
          statusEl.textContent = `연결할 수 없습니다: ${url} (서버가 켜져 있는지, 주소가 맞는지 확인해주세요)`;
          throw new Error('unreachable');
        }
        if (save) window.pywebview.api.save_server_url(url);
        location.href = url;
      });
    }

    function connectTo() {
      const url = normalize(document.getElementById('urlInput').value);
      document.getElementById('status').className = '';
      tryConnect(url, true).catch(() => {});
    }

    // 다른 PC에서 처음 실행하면 기본 주소(로컬)로는 당연히 연결이 안 되므로, 입력창을
    // 바로 편집할 수 있게 포커스 + 전체 선택해서 사용자가 바로 타이핑해 바꿀 수 있게 한다.
    const urlInput = document.getElementById('urlInput');
    urlInput.focus();
    urlInput.select();

    // 연결 버튼은 API가 준비되는 대로(최대 5초 대기 후에는 강제로) 활성화한다.
    waitForApi(5000, () => {
      document.getElementById('connectBtn').disabled = false;
    });

    // 자동 연결 시도 (기본/저장된 주소로 최초 15초간 폴링) - API 준비를 기다렸다가 시작.
    const autoTarget = '__INITIAL_URL__';
    let attempts = 0;
    const tick = () => {
      if (!apiAvailable()) { if (attempts < 15) { attempts += 1; setTimeout(tick, 1000); } return; }
      attempts += 1;
      window.pywebview.api.check_reachable(autoTarget).then(ok => {
        if (ok) location.href = autoTarget;
        else if (attempts < 15) setTimeout(tick, 1000);
      });
    };
    waitForApi(5000, tick);
  </script>
</body></html>"""


def _connect_html(initial_url: str) -> str:
    return _CONNECT_HTML_TEMPLATE.replace("__INITIAL_URL__", initial_url)


def main() -> int:
    if not _webview2_installed():
        _messagebox(
            "이 프로그램을 실행하려면 'Microsoft Edge WebView2 Runtime'이 필요합니다.\n"
            "대부분의 Windows 10/11 PC에는 이미 설치되어 있지만, 이 PC에는 없는 것 같습니다.\n\n"
            f"아래 주소에서 설치한 뒤 다시 실행해주세요:\n{WEBVIEW2_DOWNLOAD_URL}",
            title="WebView2 Runtime 필요",
        )
        return 1

    try:
        import webview  # PyInstaller onefile에서 실패 시 메시지박스로 원인을 보여주기 위해 지연 임포트
    except Exception as exc:
        _messagebox(f"필수 구성요소를 불러오지 못했습니다:\n{exc}")
        return 1

    initial_url = _initial_server_url()
    start_url = initial_url if _server_reachable(initial_url) else None
    api = Api()
    try:
        webview.create_window(
            WINDOW_TITLE,
            url=start_url,
            html=None if start_url else _connect_html(initial_url),
            js_api=api,
            width=1440,
            height=920,
            min_size=(1000, 700),
        )
        webview.start()
    except Exception as exc:
        _messagebox(f"프로그램을 시작하지 못했습니다:\n{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
