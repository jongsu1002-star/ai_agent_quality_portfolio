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
    { target: '#tab-settings', title: '설정과 Docker 실행 환경', description: '연결 모드와 외부 연동을 설정하며 촬영 중 민감정보는 자동으로 가립니다.', duration: 12000 },
    { target: '#tab-run', title: '합성 데이터 업로드', description: '정답 데이터셋과 사용자 발화 테스트 케이스를 실제로 업로드합니다.', duration: 18000, action: 'upload', pauseForAction: true },
    { target: '#pipeline-run-card', title: 'QA 파이프라인 실행', description: '검색품질 중심 평가를 실행하고 단계별 진행률을 확인합니다.', duration: 25000, action: 'execute', pauseForAction: true },
    { target: '#tab-dashboard', title: '품질 대시보드와 보고서', description: '통과율, 실패 분포, 검색품질, 실행 이력과 결함보고서를 확인합니다.', duration: 20000 },
    { target: '#tab-monitoring', title: '운영 모니터링', description: '요청량, 오류율, 응답시간과 외부 서비스 Uptime을 확인합니다.', duration: 15000 },
    { target: '#monitoring-addon-nav-btn', title: '모니터링 애드온', description: 'Docker의 Prometheus·Grafana와 k6 성능시험 화면으로 이동합니다.', duration: 10000 },
    { target: '#k6-target-url', title: 'k6 성능시험', description: '로컬 /health를 1 VU·10초로 실제 실행하고 p95와 이력을 확인합니다.', duration: 25000, action: 'execute', pauseForAction: true },
    { target: '#tab-board', title: '게시판', description: '목록, 검색, 작성 화면과 VOC 데이터 수집 지점을 확인합니다.', duration: 12000 },
    { target: '#tab-voc-analysis', title: 'VOC 합성 데이터 업로드', description: '합성 VOC를 실제 업로드하고 분석 관점을 입력합니다.', duration: 18000, action: 'upload', pauseForAction: true },
    { target: '#voc-run-btn', title: 'VOC Improved 5단계 테스트', description: '의도 분류부터 독립 Judge까지 단계별 진행을 재현하며 외부 LLM은 호출하지 않습니다.', duration: 30000, action: 'execute', pauseForAction: true },
    { target: '#tab-voc-results', title: 'VOC 결과와 품질 대시보드', description: '사전 생성 합성 결과와 독립 Judge SKIPPED, 결과 이력을 확인합니다.', duration: 18000 },
    { target: '#users-tab-btn', title: '사용자 관리', description: '승인, 역할, 사용 상태를 관리하며 촬영에서는 실제 상태를 변경하지 않습니다.', duration: 10000 },
    { target: '#error-log-tab-btn', title: '오류 로그', description: '내부 오류 원인과 실행 실패를 관리자 화면에서 추적합니다.', duration: 10000 },
    { target: '#ip-allowlist-tab-btn', title: '접근 허용 IP', description: 'LAN과 공개 배포 환경의 접근 범위를 안전하게 제어합니다.', duration: 10000 },
    { target: '.tabs', title: '통합 품질 운영 플랫폼', description: 'QA, VOC, 보고서, 성능시험과 운영 안전성을 하나의 Docker 스택에서 관리합니다.', duration: 10000 }
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

  function isTargetAvailable(target) {
    if (!target) return false;
    return target.offsetParent !== null || (typeof target.getClientRects === 'function' && target.getClientRects().length > 0);
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
    const candidate = document.querySelector(step.target);
    const target = isTargetAvailable(candidate) ? candidate : null;
    missing.hidden = Boolean(target);
    missing.textContent = target ? '' : (step.target.includes('-tab-btn')
      ? '관리자 세션이 필요합니다. 로그인 상태를 확인해 주세요.'
      : '대상 화면으로 이동한 뒤 다음 버튼을 눌러주세요.');
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

  function withDemoQuery(rawHref) {
    const url = new URL(rawHref, window.location.href);
    url.searchParams.set('demo', '1');
    return `${url.pathname}${url.search}${url.hash}`;
  }

  function preserveDemoNavigation() {
    document.querySelectorAll('a[href^="/"]').forEach((link) => {
      link.setAttribute('href', withDemoQuery(link.getAttribute('href')));
    });
    document.querySelectorAll('[onclick*="window.location.href"]').forEach((button) => {
      const value = button.getAttribute('onclick');
      button.setAttribute('onclick', value.replace(
        /window\.location\.href='([^']+)'/,
        (_match, href) => `window.location.href='${withDemoQuery(href)}'`
      ));
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
    enabled, steps, maskSelectors, start, stop, next, previous, togglePlayback, goTo, withDemoQuery, isTargetAvailable,
    get currentIndex() { return currentIndex; }
  };

  if (enabled) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
  }
}());
