// SPDX-License-Identifier: AGPL-3.0-only
// Browsers without service workers (or insecure origins) simply skip the install.
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
