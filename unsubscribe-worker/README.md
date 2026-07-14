# One-click unsubscribe endpoint

A tiny Cloudflare Worker (free plan) that gives the brief a true **RFC 8058
one-click unsubscribe** — the header Gmail and Outlook key their built-in
top-of-message *Unsubscribe* button on, and part of Gmail/Yahoo's bulk-sender
requirements. Unsubscribed addresses are stored in Workers KV, and the daily
send fetches the list and drops matching recipients automatically — no manual
secret editing to honor an unsubscribe.

## Deploy (one time, ~5 minutes)

Requires a free [Cloudflare account](https://dash.cloudflare.com/sign-up) and
Node.js. From this directory:

```bash
# 1. Log in and create the KV namespace that stores unsubscribed addresses.
npx wrangler login
npx wrangler kv namespace create UNSUBS
#    ...paste the printed `id` into wrangler.toml (REPLACE_WITH_YOUR_KV_NAMESPACE_ID).

# 2. Set the secret that protects the /list endpoint (any long random string).
npx wrangler secret put LIST_SECRET

# 3. Deploy. Note the printed URL, e.g. https://cre-brief-unsubscribe.<you>.workers.dev
npx wrangler deploy
```

## Wire it into the brief

Set these GitHub Actions **secrets** (Settings → Secrets and variables →
Actions), using your worker's URL:

| Secret | Value |
| --- | --- |
| `LIST_UNSUBSCRIBE` | `https://cre-brief-unsubscribe.<you>.workers.dev/?email={email}` |
| `UNSUBSCRIBE_FEED_URL` | `https://cre-brief-unsubscribe.<you>.workers.dev/list` |
| `UNSUBSCRIBE_FEED_SECRET` | the same value you gave `wrangler secret put LIST_SECRET` |

The `{email}` placeholder is substituted per recipient at send time (URL-
encoded), so a one-click POST identifies exactly who unsubscribed.

That's it. Every send now:

1. emits `List-Unsubscribe: <https://…/?email=recipient>` **and**
   `List-Unsubscribe-Post: List-Unsubscribe=One-Click`;
2. points the footer **Unsubscribe** button at the same per-recipient URL
   (humans get a confirmation page — nothing unsubscribes on a bare GET, so
   corporate link-scanners can't unsubscribe people by prefetching);
3. fetches `/list` before sending and silently drops anyone on it. If the
   worker is unreachable the send **aborts** rather than risk mailing someone
   who already unsubscribed.

## Endpoints

| Route | Behavior |
| --- | --- |
| `POST /?email=a@b.com` | Unsubscribes the address (RFC 8058 one-click target). |
| `GET /?email=a@b.com` | Human confirmation page with an Unsubscribe button. |
| `GET /list` | JSON array of unsubscribed addresses. Requires `Authorization: Bearer <LIST_SECRET>`. |

To **re-subscribe** someone, delete their key:
`npx wrangler kv key delete --binding UNSUBS "person@example.com" --remote`
