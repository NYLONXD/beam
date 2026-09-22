# beam relay

A short-lived drop box for beam's encrypted project zips, on your own
Cloudflare account. Deploying it gives beam a host that answers to you instead
of to whichever free file host happens to be up — and one that deletes the zip
the moment the other laptop confirms it arrived.

```
beam send my_app  ──►  Worker  ──►  R2        (the encrypted zip)
                          └─────►  Upstash    (a ~200 byte record + the TTL)
```

The Worker never sees your files in the clear. beam encrypts the zip with
AES-256-GCM before uploading, and the key is the half of the code after the
dash, which never leaves the sender's laptop.

**The Redis record is what expires a code.** When its TTL runs out the id is
unreachable, whatever is still in R2. The R2 lifecycle rule below is only a
janitor for bytes nobody collected — its day-level granularity never shows,
because the record is already gone.

## Deploy it

You need a Cloudflare account and an [Upstash](https://upstash.com) Redis
database. Both have free tiers that cover this comfortably.

```bash
cd relay
npm install                                   # just wrangler
npx wrangler login

npx wrangler r2 bucket create beam-relay      # the bucket in wrangler.toml
npx wrangler secret put UPSTASH_REDIS_REST_URL    # from the Upstash console
npx wrangler secret put UPSTASH_REDIS_REST_TOKEN  # the REST token, not the password

npx wrangler deploy
```

`deploy` prints your URL, something like
`https://beam-relay.<your-subdomain>.workers.dev`. Check it:

```bash
curl https://beam-relay.<your-subdomain>.workers.dev/v1/health
# {"ok":true,"service":"beam-relay","maxBytes":10485760,"keeps":"5 hours"}
```

Then add the lifecycle rule that sweeps uncollected bytes — the dashboard
under **R2 → beam-relay → Settings → Object lifecycle rules**, or:

```bash
npx wrangler r2 bucket lifecycle add beam-relay --name expire --expire-days 1
```

One day is deliberate: it is comfortably longer than the 5 hour TTL, so it only
ever removes objects whose record died without anyone collecting them.

## Point beam at it

Per machine:

```bash
export BEAM_RELAY=https://beam-relay.<your-subdomain>.workers.dev    # bash
$env:BEAM_RELAY = "https://beam-relay.<your-subdomain>.workers.dev"  # PowerShell
```

Or bake it into your own build, so nobody has to set anything — put the URL in
`DEFAULT_URL` on `BeamRelay` in `src/beam/_cloud.py`.

Either way beam tries your relay first and falls back to the public hosts if it
is down, so a broken deploy degrades rather than breaks. Codes from your relay
start with `w`.

**The receiver needs `BEAM_RELAY` set too.** Unlike the public hosts, a relay
code is only fetchable from the relay that issued it — `beam receive` says so
plainly when it is not set. Baking the URL in is what avoids this.

## Settings

All in `wrangler.toml` under `[vars]`, all changeable with a redeploy.

| Var | Default | What it does |
| --- | --- | --- |
| `TTL_SECONDS` | `18000` (5h) | how long a code works — the number users feel |
| `MAX_BODY_BYTES` | `10485760` (10 MB) | biggest zip accepted |
| `MAX_LIVE` | `500` | transfers in flight at once |
| `RATE_PER_HOUR` | `20` | uploads per IP per hour |

`MAX_BODY_BYTES × MAX_LIVE` is your worst-case R2 usage: 5 GB at the defaults,
inside the 10 GB free tier. Raise one and check the other.

Bytes are streamed to R2 rather than buffered, so raising `MAX_BODY_BYTES` will
not blow the Worker's 128 MB of memory — but Cloudflare caps request bodies at
100 MB on the free and pro plans, which is the real ceiling.

## Abuse

Once your URL is baked into a published build, `/v1/up` is a public endpoint
that accepts opaque encrypted blobs. It cannot be moderated by design. The caps
above are the whole defence, so keep them tight and watch usage:

```bash
npx wrangler tail            # live logs
```

If it is ever abused, redeploy with `RATE_PER_HOUR` at `0` to close uploads
while leaving pickups working, or `npx wrangler delete` to take it down — beam
falls back to the public hosts either way.

## Tests

```bash
npm test          # or: node --test "test/**/*.test.js"
```

Eleven tests covering the round trip, burn-after-read, TTL expiry, every cap,
routing and a Redis outage. R2 and Upstash are stubbed, so they need no
network, no account and no `npm install`. CI runs them on every push.

## The API

| | |
| --- | --- |
| `POST /v1/up` | body is the ciphertext → `{ id, keeps, expires }` |
| `GET /v1/<id>` | the ciphertext back, or `410` once it is gone |
| `POST /v1/<id>/done` | receiver confirms; burn it now rather than at TTL |
| `GET /v1/health` | `{ ok, maxBytes, keeps }`, for beam's liveness probe |
