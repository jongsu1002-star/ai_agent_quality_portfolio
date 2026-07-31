(() => {
  const storageKey = "qa-ui-theme";
  const allowedThemes = new Set(["ocean", "forest", "violet", "sunset", "pink"]);

  function readTheme() {
    try {
      const saved = localStorage.getItem(storageKey);
      return allowedThemes.has(saved) ? saved : "ocean";
    } catch (_) {
      return "ocean";
    }
  }

  function applyTheme(theme) {
    const next = allowedThemes.has(theme) ? theme : "ocean";
    document.documentElement.classList.add("ui-theme-switching");
    document.documentElement.dataset.uiTheme = next;
    document.querySelectorAll("[data-ui-theme-select]").forEach((select) => {
      select.value = next;
    });
    try {
      localStorage.setItem(storageKey, next);
    } catch (_) {}
    requestAnimationFrame(() => {
      requestAnimationFrame(() => document.documentElement.classList.remove("ui-theme-switching"));
    });
  }

  applyTheme(readTheme());
  document.addEventListener("DOMContentLoaded", () => {
    applyTheme(readTheme());
    document.querySelectorAll("[data-ui-theme-select]").forEach((select) => {
      select.addEventListener("change", () => applyTheme(select.value));
    });
  });
})();
