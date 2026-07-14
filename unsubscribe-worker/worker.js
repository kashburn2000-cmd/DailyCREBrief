/**
 * CRE Finance Brief — one-click unsubscribe endpoint (Cloudflare Worker).
 *
 * Three routes, all on the free plan (Workers + KV):
 *
 *   POST /?email=a@b.com   RFC 8058 one-click. Gmail/Outlook POST here when a
 *                          user taps their built-in Unsubscribe button; the
 *                          footer button's confirmation form posts here too.
 *                          Stores the address in KV and returns 200.
 *   GET  /?email=a@b.com   A human clicked the footer button. Shows a one-tap
 *                          confirmation page (never unsubscribe on GET — link
 *                          scanners prefetch GETs and would unsubscribe
 *                          everyone whose mail is scanned).
 *   GET  /list             The full unsubscribed list as a JSON array. Requires
 *                          `Authorization: Bearer <LIST_SECRET>`. The daily
 *                          send fetches this and drops matching recipients.
 *
 * Bindings (see wrangler.toml): UNSUBS (KV namespace), LIST_SECRET (secret).
 */

const PAGE_STYLE =
  "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;" +
  "max-width:420px;margin:80px auto;padding:0 20px;color:#1a1a1a;line-height:1.5;";

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function page(body, status = 200) {
  return new Response(
    `<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CRE Finance Brief</title>
<body style="${PAGE_STYLE}">
<h2 style="font-size:20px;">CRE Finance Brief</h2>
${body}
</body></html>`,
    { status, headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
}

function normalizeEmail(raw) {
  const email = (raw || "").trim().toLowerCase();
  // Light shape check only — this must accept anything we might have mailed.
  return email.includes("@") && email.length <= 254 ? email : "";
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/list") {
      const auth = request.headers.get("Authorization") || "";
      if (!env.LIST_SECRET || auth !== `Bearer ${env.LIST_SECRET}`) {
        return new Response("forbidden", { status: 403 });
      }
      const emails = [];
      let cursor;
      do {
        const batch = await env.UNSUBS.list({ cursor });
        emails.push(...batch.keys.map((k) => k.name));
        cursor = batch.list_complete ? undefined : batch.cursor;
      } while (cursor);
      return Response.json(emails);
    }

    if (url.pathname !== "/") return new Response("not found", { status: 404 });

    let email = normalizeEmail(url.searchParams.get("email"));

    if (request.method === "POST") {
      // The confirmation form carries the address in its body; one-click
      // providers carry it in the query string (the per-recipient URL).
      if (!email) {
        try {
          const form = await request.formData();
          email = normalizeEmail(form.get("email"));
        } catch {
          // Not form-encoded (e.g. a bare one-click POST body) — query only.
        }
      }
      if (!email) return new Response("missing email", { status: 400 });
      await env.UNSUBS.put(email, new Date().toISOString());
      return page(
        `<p><strong>${escapeHtml(email)}</strong> is unsubscribed.</p>
<p style="color:#6b7280;">You'll receive no further editions. Resubscribe any
time by asking the sender to re-add you.</p>`,
      );
    }

    if (request.method !== "GET") {
      return new Response("method not allowed", { status: 405 });
    }

    const emailField = email
      ? `<input type="hidden" name="email" value="${escapeHtml(email)}">
<p>Unsubscribe <strong>${escapeHtml(email)}</strong> from the daily brief?</p>`
      : `<p>Enter your address to unsubscribe from the daily brief:</p>
<p><input type="email" name="email" required placeholder="you@example.com"
style="padding:8px 10px;font-size:14px;border:1px solid #e5e7eb;border-radius:6px;width:100%;box-sizing:border-box;"></p>`;

    return page(
      `<form method="post" action="/">
${emailField}
<button type="submit"
style="padding:10px 24px;font-size:14px;font-weight:600;color:#ffffff;background:#0b3d5c;border:none;border-radius:6px;cursor:pointer;">
Unsubscribe</button>
</form>`,
    );
  },
};
