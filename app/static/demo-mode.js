(function () {
  'use strict';

  const STEP_KEY = 'qa-demo-step';
  const PLAY_KEY = 'qa-demo-playing';
  const enabled = new URLSearchParams(window.location.search).get('demo') === '1';
  const maskSelectors = [
    'input[type="password"]', '#openai-key', '#anthropic-key', '#llm-key-value',
    '#slack-webhook', '#discord-webhook', '#teams-webhook', '#jira-base-url',
    '#jira-email', '#jira-token', '#my-ip-display'
  ];
  const steps = [
    { target: '.tabs', title: 'AI Agent 품질관리 플랫폼', description: '설정, 품질검증, 분석, 운영 모니터링을 하나의 흐름으로 통합했습니다.', duration: 9000 },
    { target: '#connection-settings-card', title: '연결 설정', description: 'OpenAI·Anthropic·사내 모델과 Jira·협업 웹훅을 연결합니다. 민감정보는 데모 모드에서 자동으로 가립니다.', duration: 11000 },
    { target: '#file', title: '정답 기준 데이터셋', description: '정답, 카테고리, 필수 키워드를 담은 합성 데이터셋을 업로드합니다.', duration: 9000, action: 'upload', pauseForAction: true },
    { target: '#file-testcase', title: '사용자 발화 테스트 케이스', description: '기준값과 질문을 분리해 같은 기준으로 다양한 표현을 반복 검증합니다.', duration: 9000, action: 'upload', pauseForAction: true },
    { target: '#pipeline-run-card', title: 'QA 파이프라인 실행', description: '검색품질, 근거성, LLM 판정을 실행하고 단계별 진행률을 확인합니다.', duration: 15000, action: 'execute', pauseForAction: true },
    { target: '#tab-dashboard', title: '품질 대시보드', description: '통과율, 카테고리별 실패, 검색품질과 결함보고서를 차트와 표로 확인합니다.', duration: 17000 },
    { target: '#run-history', title: '보고서와 실행 이력', description: 'CSV·마크다운 결과와 실행 조건을 남겨 회귀검증과 조치 추적에 활용합니다.', duration: 12000 },
    { target: '#tab-voc-analysis', title: 'VOC 분석', description: '합성 VOC 파일을 입력해 반복 불만과 우선 개선 과제를 분석합니다.', duration: 15000, action: 'upload', pauseForAction: true },
    { target: '#tab-voc-results', title: 'VOC 대체 결과', description: '외부 API 제한에 대비한 사전 생성 합성 데모 결과이며 독립 Judge는 SKIPPED로 표시합니다.', duration: 16000 },
    { target: '#tab-monitoring', title: '운영 모니터링', description: '요청량, 오류율, 응답시간과 외부 서비스 Uptime을 함께 확인합니다.', duration: 15000 },
    { target: '#k6-target-url', title: 'k6 성능시험', description: '로컬 /health를 1 VU·10초로 실행하고 p95 응답시간과 이력을 확인합니다.', duration: 15000, action: 'execute', pauseForAction: true },
    { target: '.admin-nav-label', title: '운영 안전성과 마무리', description: '권한 관리, 오류 로그, 접근 허용 IP까지 실제 운영을 고려한 품질관리 시스템입니다.', duration: 11000 }
  ];

  let currentIndex = Math.max(0, Math.min(steps.length - 1, Number(sessionStorage.getItem(STEP_KEY) || 0)));
  let playing = sessionStorage.getItem(PLAY_KEY) === '1';
  let timer = null;
  let started = false;
  let highlighted = null;
  let caption = null;

  function applyMasks() {
    maskSelectors.forEach((selector) => {
      document.querySelectorAll(selector).forEach((element) => element.classList.add('qa-demo-mask'));
    });
  }

  function clearHighlight() {
    if (highlighted) highlighted.classList.remove('qa-demo-highlight');
    highlighted = null;
  }

  function ensureOverlay() {
    if (caption) return;
    caption = document.createElement('aside');
    caption.className = 'qa-demo-caption';
    caption.setAttribute('role', 'status');
    caption.setAttribute('aria-live', 'polite');
    caption.innerHTML = [
      '<strong data-demo-title></strong>', '<p data-demo-description></p>',
      '<div class="qa-demo-missing" data-demo-missing hidden></div>',
      '<div class="qa-demo-controls">',
      '<button type="button" data-demo-previous aria-label="이전 단계">◀</button>',
      '<button type="button" data-demo-play aria-label="재생 또는 일시정지">▶</button>',
      '<span class="qa-demo-step-count" data-demo-count></span>',
      '<button type="button" data-demo-next aria-label="다음 단계">▶|</button>',
      '<button type="button" data-demo-exit aria-label="데모 종료">✕</button>',
      '</div>'
    ].join('');
    caption.querySelector('[data-demo-previous]').addEventListener('click', previous);
    caption.querySelector('[data-demo-play]').addEventListener('click', togglePlayback);
    caption.querySelector('[data-demo-next]').addEventListener('click', next);
    caption.querySelector('[data-demo-exit]').addEventListener('click', stop);
    document.body.appendChild(caption);
  }

  function render() {
    if (!started) return;
    clearTimeout(timer);
    clearHighlight();
    applyMasks();
    ensureOverlay();
    const step = steps[currentIndex];
    caption.querySelector('[data-demo-title]').textContent = step.title;
    caption.querySelector('[data-demo-description]').textContent = step.description;
    caption.querySelector('[data-demo-count]').textContent = `${currentIndex + 1} / ${steps.length}`;
    caption.querySelector('[data-demo-play]').textContent = playing ? 'Ⅱ' : '▶';
    const missing = caption.querySelector('[data-demo-missing]');
    const target = document.querySelector(step.target);
    missing.hidden = Boolean(target);
    missing.textContent = target ? '' : '대상 화면으로 이동한 뒤 다음 버튼을 눌러주세요.';
    if (target) {
      target.classList.add('qa-demo-highlight');
      highlighted = target;
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    sessionStorage.setItem(STEP_KEY, String(currentIndex));
    sessionStorage.setItem(PLAY_KEY, playing ? '1' : '0');
    if (playing && !step.pauseForAction && currentIndex < steps.length - 1) {
      timer = setTimeout(next, step.duration || 10000);
    } else if (step.pauseForAction) {
      playing = false;
      sessionStorage.setItem(PLAY_KEY, '0');
      caption.querySelector('[data-demo-play]').textContent = '▶';
    }
  }

  function goTo(index) {
    currentIndex = Math.max(0, Math.min(steps.length - 1, Number(index) || 0));
    sessionStorage.setItem(STEP_KEY, String(currentIndex));
    render();
  }

  function next() { goTo(currentIndex + 1); }
  function previous() { goTo(currentIndex - 1); }
  function togglePlayback() { playing = !playing; render(); }

  function preserveDemoNavigation() {
    document.querySelectorAll('a[href^="/"]').forEach((link) => {
      const url = new URL(link.getAttribute('href'), window.location.href);
      url.searchParams.set('demo', '1');
      link.setAttribute('href', `${url.pathname}${url.search}${url.hash}`);
    });
    document.querySelectorAll('[onclick*="window.location.href"]').forEach((button) => {
      const value = button.getAttribute('onclick');
      button.setAttribute('onclick', value.replace(/window\.location\.href='\/([^']*)'/, "window.location.href='/?demo=1#$1'"));
    });
  }

  function ripple(event) {
    const marker = document.createElement('span');
    marker.className = 'qa-demo-ripple';
    marker.style.left = `${event.clientX}px`;
    marker.style.top = `${event.clientY}px`;
    document.body.appendChild(marker);
    setTimeout(() => marker.remove(), 700);
  }

  function start() {
    if (!enabled || started) return;
    started = true;
    document.body.classList.add('qa-demo-active');
    document.documentElement.addEventListener('click', ripple, true);
    preserveDemoNavigation();
    render();
  }

  function stop() {
    playing = false;
    clearTimeout(timer);
    clearHighlight();
    document.body.classList.remove('qa-demo-active');
    document.querySelectorAll('.qa-demo-mask').forEach((element) => element.classList.remove('qa-demo-mask'));
    document.documentElement.removeEventListener('click', ripple, true);
    if (caption) caption.remove();
    caption = null;
    started = false;
    sessionStorage.removeItem(STEP_KEY);
    sessionStorage.removeItem(PLAY_KEY);
    const url = new URL(window.location.href);
    url.searchParams.delete('demo');
    window.location.href = `${url.pathname}${url.search}${url.hash}`;
  }

  window.QADemoMode = {
    enabled, steps, maskSelectors, start, stop, next, previous, togglePlayback, goTo,
    get currentIndex() { return currentIndex; }
  };

  if (enabled) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
  }
}());
