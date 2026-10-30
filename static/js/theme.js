// SPDX-License-Identifier: AGPL-3.0-only
// Run before stylesheets so the first paint follows the chosen theme: Auto follows the system, Light and Dark are fixed.
(() => {
  const KEY = "printhub-theme";
  const dark = matchMedia("(prefers-color-scheme: dark)");
  let choice = "auto";
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === "light" || stored === "dark") choice = stored;
  } catch { /* storage is blocked: stay on Auto */ }
  const apply = () => document.documentElement.setAttribute("data-bs-theme", choice === "auto" ? (dark.matches ? "dark" : "light") : choice);
  apply();
  dark.addEventListener("change", apply);
  window.printhubTheme = {
    get: () => choice,
    set(next) {
      choice = next === "light" || next === "dark" ? next : "auto";
      try {
        localStorage.setItem(KEY, choice);
      } catch { /* private mode: the choice lasts for this page only */ }
      apply();
    },
  };
})();
