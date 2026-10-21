// SPDX-License-Identifier: AGPL-3.0-only
import { apiFetch, errorMessage } from "./api.js";

async function refreshPrinter() {
  const banner = document.getElementById("printer-banner");
  try {
    const res = await apiFetch("/api/printer");
    if (!res.ok) throw new Error(await errorMessage(res));
    const p = await res.json();
    const reasons = p.state_reasons.join(", ");
    banner.hidden = !(p.state === "stopped" || reasons);
    banner.textContent = `Printer ${p.state}${reasons ? `: ${reasons}` : ""}`;
  } catch (e) {
    banner.hidden = false;
    banner.textContent = `Printer status unavailable: ${e.message}`;
  }
}

refreshPrinter();
setInterval(refreshPrinter, 10_000);
