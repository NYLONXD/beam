/**
 * beam relay - a short-lived drop box for encrypted project zips.
 *
 * The zip arrives already encrypted: the key is half of the code the sender
 * reads out and it never reaches this Worker, so everything here is opaque
 * bytes. That is deliberate - it also means this relay cannot inspect or
 * moderate what it carries, which is why the caps below matter.
 *
 * Bytes live in R2. Upstash Redis holds only a tiny record per transfer, and
 * its TTL is what actually expires a code: once the record is gone the id is
 * unreachable, whatever is still sitting in R2. An R2 lifecycle rule sweeps
 * those leftovers a day later.
 *
 *   POST /v1/up          body = ciphertext        -> { id, keeps, expires }
 *   GET  /v1/<id>        the ciphertext back, or 410 once it is gone
 *   POST /v1/<id>/done   receiver confirms; burn it now rather than at TTL
 *   GET  /v1/health      for beam's "is this thing up?" probe
 */

const DEFAULTS = {
  TTL_SECONDS: 5 * 60 * 60, // how long a code works
  MAX_BODY_BYTES: 10 * 1024 * 1024,
  MAX_LIVE: 500, // live transfers at once; x MAX_BODY_BYTES bounds storage
  RATE_PER_HOUR: 20, // uploads per IP
};

const LIVE = "beam:live"; // sorted set of id -> expiry, for the live count
const RECORD = (id) => `beam:s:${id}`;
const ID_PATTERN = /^[0-9a-f]{8,64}$/;

/**
 * A numeric setting from wrangler.toml [vars], falling back to DEFAULTS.
 *
 * An explicit "0" is honoured - RATE_PER_HOUR = "0" is how you close the relay
 * to new uploads while letting pickups finish - but a blank or unparseable
 * value falls back rather than quietly meaning zero, since Number("") is 0.
 */
function conf(env, name) {
  const raw = env[name];
  if (raw === undefined || raw === null || String(raw).trim() === "") {
    return DEFAULTS[name];
  }
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : DEFAULTS[name];
}

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

const oops = (status, error) => json({ error }, status);

const now = () => Math.floor(Date.now() / 1000);

function newId() {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** "18000" -> "5 hours", for the line beam prints to the sender. */
function describe(seconds) {
  const hours = Math.round(seconds / 3600);
  if (hours >= 1) return hours === 1 ? "1 hour" : `${hours} hours`;
  const minutes = Math.max(1, Math.round(seconds / 60));
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

// --- Upstash Redis, over its REST API (no npm dependency needed) -----------

async function pipeline(env, commands) {
  const response = await fetch(`${env.UPSTASH_REDIS_REST_URL}/pipeline`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.UPSTASH_REDIS_REST_TOKEN}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(commands),
  });
  if (!response.ok) throw new Error(`redis answered ${response.status}`);
  const rows = await response.json();
  return rows.map((row) => {
    if (row.error) throw new Error(`redis: ${row.error}`);
    return row.result;
  });
}

const redis = async (env, ...command) => (await pipeline(env, [command]))[0];

// --- handlers --------------------------------------------------------------

async function health(env) {
  return json({
    ok: true,
    service: "beam-relay",
    maxBytes: conf(env, "MAX_BODY_BYTES"),
    keeps: describe(conf(env, "TTL_SECONDS")),
  });
}

async function upload(request, env) {
  const ttl = conf(env, "TTL_SECONDS");
  const maxBody = conf(env, "MAX_BODY_BYTES");

  // Reject on the declared length before reading a byte. It can lie, so the
  // real size is checked again once R2 has it.
  const declared = Number(request.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > maxBody) {
    return oops(413, `too big: ${declared} bytes, the limit here is ${maxBody}`);
  }

  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const window = Math.floor(Date.now() / 1000 / 3600);
  const rateKey = `beam:rl:${ip}:${window}`;
  const [hits] = await pipeline(env, [
    ["INCR", rateKey],
    ["EXPIRE", rateKey, "3600"],
    ["ZREMRANGEBYSCORE", LIVE, "0", String(now())], // forget what has expired
  ]);
  if (hits > conf(env, "RATE_PER_HOUR")) {
    return oops(429, "too many uploads from this address; try again later");
  }

  const live = await redis(env, "ZCARD", LIVE);
  if (live >= conf(env, "MAX_LIVE")) {
    return oops(503, "the relay is full right now; try again in a few minutes");
  }

  const id = newId();
  // Streamed, not buffered: raising MAX_BODY_BYTES must not put the whole zip
  // in the Worker's 128 MB of memory.
  await env.BEAM.put(id, request.body);
  const stored = await env.BEAM.head(id);
  if (!stored || stored.size === 0) {
    await env.BEAM.delete(id);
    return oops(400, "nothing arrived");
  }
  if (stored.size > maxBody) {
    await env.BEAM.delete(id);
    return oops(413, `too big: ${stored.size} bytes, the limit here is ${maxBody}`);
  }

  const expires = now() + ttl;
  await pipeline(env, [
    ["SET", RECORD(id), JSON.stringify({ bytes: stored.size, expires }), "EX", String(ttl)],
    ["ZADD", LIVE, String(expires), id],
  ]);
  return json({ id, keeps: describe(ttl), expires });
}

async function download(id, env) {
  // The Redis record is the source of truth: no record, no transfer, even if
  // the bytes are still in R2 waiting for the lifecycle rule.
  const raw = await redis(env, "GET", RECORD(id));
  if (!raw) return oops(410, "this code has expired or was already collected");

  const record = JSON.parse(raw);
  const object = await env.BEAM.get(id);
  if (!object) return oops(410, "this code has expired or was already collected");

  return new Response(object.body, {
    headers: {
      "content-type": "application/octet-stream",
      "content-length": String(record.bytes),
      "x-beam-expires": String(record.expires),
    },
  });
}

async function done(id, env) {
  await pipeline(env, [
    ["DEL", RECORD(id)],
    ["ZREM", LIVE, id],
  ]);
  await env.BEAM.delete(id);
  return json({ ok: true });
}

// --- routing ---------------------------------------------------------------

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname.replace(/\/+$/, "");
    const method = request.method;
    try {
      if (method === "GET" && path === "/v1/health") return await health(env);
      if (method === "POST" && path === "/v1/up") return await upload(request, env);

      const match = path.match(/^\/v1\/([0-9a-f]+)(\/done)?$/);
      if (match && ID_PATTERN.test(match[1])) {
        const [, id, isDone] = match;
        if (method === "GET" && !isDone) return await download(id, env);
        if (method === "POST" && isDone) return await done(id, env);
      }
      return oops(404, "no such endpoint");
    } catch (err) {
      return oops(502, `relay error: ${err.message}`);
    }
  },
};
