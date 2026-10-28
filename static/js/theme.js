// SPDX-License-Identifier: AGPL-3.0-only
// Run before stylesheets so the first paint follows the system theme too.
(() => {
  const dark = matchMedia("(prefers-color-scheme: dark)");
  const apply = () => document.documentElement.setAttribute("data-bs-theme", dark.matches ? "dark" : "light");
  apply();
  dark.addEventListener("change", apply);
})();
