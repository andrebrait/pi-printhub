// SPDX-License-Identifier: AGPL-3.0-only
// Swagger UI sends same-origin requests with the browser's session cookie by default, so Try it out needs no extra wiring.
SwaggerUIBundle({
  url: "/api/openapi.json",
  dom_id: "#swagger",
  persistAuthorization: false, // a token typed under Authorize is not kept in localStorage
  tryItOutEnabled: true,
});
