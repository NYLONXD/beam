/**
 * Exercises the relay Worker with R2 and Upstash Redis stubbed out.
 *
 *     cd relay && npm test
 *
 * The stubs implement only the handful of commands the Worker actually uses,
 * but they enforce TTL and the sorted set properly, so expiry and the live
 * count are really tested rather than assumed.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import worker from "../src/worker.js";

// --- a Redis that understands the six commands the Worker sends -------------

class FakeRedis {
  /** @param clock seconds since the epoch, shared with the Worker's Date.now */
  constructor(clock) {
    this.values = new Map(); // key -> { value, expiresAt|null }
    this.zsets = new Map(); // key -> Map(member -> score)
    this.clock = clock;
  }

  get now() {
    return this.clock();
  }

  live(key) {
    const entry = this.values.get(key);
    if (!entry) return undefined;
    if (entry.expiresAt !== null && entry.expiresAt <= this.now) {
      this.values.delete(key);
      return undefined;
    }
    return entry;
  }

  run([name, ...args]) {
    const zset = (key) => {
      if (!this.zsets.has(key)) this.zsets.set(key, new Map());
      return this.zsets.get(key);
    };
    switch (name.toUpperCase()) {
      case "SET": {
        const [key, value, , seconds] = args;
        const ttl = seconds === undefined ? null : this.now + Number(seconds);
        this.values.set(key, { value, expiresAt: ttl });
        return "OK";
      }
      case "GET":
        return this.live(args[0])?.value ?? null;
      case "DEL":
        return this.values.delete(args[0]) ? 1 : 0;
      case "INCR": {
        const entry = this.live(args[0]);
        const next = Number(entry?.value ?? 0) + 1;
        this.values.set(args[0], {
          value: String(next),
          expiresAt: entry?.expiresAt ?? null,
        });
        return next;
      }
      case "EXPIRE": {
        const entry = this.live(args[0]);
        if (!entry) return 0;
        entry.expiresAt = this.now + Number(args[1]);
        return 1;
      }
      case "ZADD":
        zset(args[0]).set(args[2], Number(args[1]));
        return 1;
      case "ZREM":
        return zset(args[0]).delete(args[1]) ? 1 : 0;
      case "ZCARD":
        return zset(args[0]).size;
      case "ZREMRANGEBYSCORE": {
        const [key, min, max] = args;
        const members = zset(key);
        let removed = 0;
        for (const [member, score] of [...members]) {
          if (score >= Number(min) && score <= Number(max)) {
            members.delete(member);
            removed += 1;
          }
        }
        return removed;
      }
      default:
        throw new Error(`fake redis does not know ${name}`);
    }
  }
}

class FakeR2 {
  constructor() {
    this.objects = new Map();
  }

  async put(key, body) {
    const bytes =
      body instanceof ReadableStream
        ? new Uint8Array(await new Response(body).arrayBuffer())
        : new Uint8Array(body);
    this.objects.set(key, bytes);
  }

  async head(key) {
    const bytes = this.objects.get(key);
    return bytes ? { size: bytes.length } : null;
  }

  async get(key) {
    const bytes = this.objects.get(key);
    return bytes ? { body: new Response(bytes).body } : null;
  }

  async delete(key) {
    this.objects.delete(key);
  }
}

function harness(vars = {}) {
  // One clock for both sides: the Worker scores the live set with Date.now(),
  // so the fake's TTLs have to move with it or expiry cannot be tested.
  let nowMs = 1_700_000_000_000;
  Date.now = () => nowMs;
  const advance = (seconds) => {
    nowMs += seconds * 1000;
  };

  const redis = new FakeRedis(() => Math.floor(nowMs / 1000));
  const r2 = new FakeR2();
  const env = {
    BEAM: r2,
    UPSTASH_REDIS_REST_URL: "https://fake.upstash.io",
    UPSTASH_REDIS_REST_TOKEN: "token",
    ...vars,
  };

  globalThis.fetch = async (url, init) => {
    assert.equal(url, "https://fake.upstash.io/pipeline");
    assert.equal(init.headers.authorization, "Bearer token");
    const rows = JSON.parse(init.body).map((command) => {
      try {
        return { result: redis.run(command) };
      } catch (err) {
        return { error: err.message };
      }
    });
    return new Response(JSON.stringify(rows), { status: 200 });
  };

  const call = (path, init = {}) =>
    worker.fetch(
      new Request(`https://relay.test${path}`, {
        headers: { "cf-connecting-ip": "203.0.113.7" },
        ...init,
      }),
      env,
    );

  const send = (body) =>
    call("/v1/up", { method: "POST", body, duplex: "half" });

  return { call, send, redis, r2, env, advance };
}

// --- the happy path ---------------------------------------------------------

test("a zip goes up, comes back, and is burned on confirmation", async () => {
  const { call, send, redis, r2 } = harness();
  const payload = new Uint8Array([1, 2, 3, 4, 5]);

  const up = await send(payload);
  assert.equal(up.status, 200);
  const { id, keeps } = await up.json();
  assert.match(id, /^[0-9a-f]{24}$/);
  assert.equal(keeps, "5 hours");
  assert.equal(r2.objects.size, 1);

  const down = await call(`/v1/${id}`);
  assert.equal(down.status, 200);
  assert.deepEqual(new Uint8Array(await down.arrayBuffer()), payload);
  assert.equal(down.headers.get("content-length"), "5");

  const done = await call(`/v1/${id}/done`, { method: "POST" });
  assert.equal(done.status, 200);
  assert.equal(r2.objects.size, 0, "R2 object deleted");
  assert.equal(redis.values.size, 1, "only the rate-limit key is left");

  assert.equal((await call(`/v1/${id}`)).status, 410, "second pickup refused");
});

test("the id is unreachable once the record's TTL passes", async () => {
  const { call, send, r2, advance } = harness({ TTL_SECONDS: "3600" });
  const { id } = await (await send(new Uint8Array([9]))).json();

  assert.equal((await call(`/v1/${id}`)).status, 200);

  advance(3601); // the record expires; R2 still holds the bytes
  assert.equal(r2.objects.size, 1, "lifecycle rule has not swept it yet");

  const late = await call(`/v1/${id}`);
  assert.equal(late.status, 410);
  assert.match((await late.json()).error, /expired/);
});

test("TTL_SECONDS is reflected in what the sender is told", async () => {
  const { send } = harness({ TTL_SECONDS: "3600" });
  assert.equal((await (await send(new Uint8Array([1]))).json()).keeps, "1 hour");
});

// --- the caps ---------------------------------------------------------------

test("an oversized body is refused and leaves nothing behind", async () => {
  const { send, r2 } = harness({ MAX_BODY_BYTES: "8" });
  const response = await send(new Uint8Array(64));

  assert.equal(response.status, 413);
  assert.match((await response.json()).error, /too big/);
  assert.equal(r2.objects.size, 0, "no orphan in R2");
});

test("an empty body is refused", async () => {
  const { send } = harness();
  assert.equal((await send(new Uint8Array(0))).status, 400);
});

test("uploads are rate limited per address", async () => {
  const { send } = harness({ RATE_PER_HOUR: "3" });
  for (let i = 0; i < 3; i++) {
    assert.equal((await send(new Uint8Array([i]))).status, 200);
  }
  const blocked = await send(new Uint8Array([4]));
  assert.equal(blocked.status, 429);
  assert.match((await blocked.json()).error, /too many uploads/);
});

test("the relay refuses new transfers once it is full", async () => {
  const { send } = harness({ MAX_LIVE: "2", RATE_PER_HOUR: "50" });
  assert.equal((await send(new Uint8Array([1]))).status, 200);
  assert.equal((await send(new Uint8Array([2]))).status, 200);

  const full = await send(new Uint8Array([3]));
  assert.equal(full.status, 503);
  assert.match((await full.json()).error, /full/);
});

test("expired transfers stop counting towards the live cap", async () => {
  const { send, advance } = harness({
    MAX_LIVE: "1",
    RATE_PER_HOUR: "50",
    TTL_SECONDS: "3600",
  });
  assert.equal((await send(new Uint8Array([1]))).status, 200);
  assert.equal((await send(new Uint8Array([2]))).status, 503);

  advance(3601);
  assert.equal((await send(new Uint8Array([3]))).status, 200, "slot freed");
});

// --- routing ----------------------------------------------------------------

test("health reports the caps without touching storage", async () => {
  const { call, r2 } = harness({ MAX_BODY_BYTES: "1234" });
  const response = await call("/v1/health");

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    ok: true,
    service: "beam-relay",
    maxBytes: 1234,
    keeps: "5 hours",
  });
  assert.equal(r2.objects.size, 0);
});

test("unknown paths, bad ids and wrong methods are 404", async () => {
  const { call } = harness();
  for (const [path, init] of [
    ["/", {}],
    ["/v1/nope", {}],
    ["/v1/NOTHEX", {}],
    ["/v1/../etc", {}],
    ["/v1/up", {}], // GET on the upload endpoint
    ["/v1/abcdef0123456789abcdef01", { method: "POST" }], // POST without /done
  ]) {
    assert.equal((await call(path, init)).status, 404, `${path} should 404`);
  }
});

test("a Redis outage surfaces as a relay error, not a bad code", async () => {
  const { call, send } = harness();
  const { id } = await (await send(new Uint8Array([1]))).json();
  globalThis.fetch = async () => new Response("nope", { status: 500 });

  const response = await call(`/v1/${id}`);
  assert.equal(response.status, 502);
  assert.match((await response.json()).error, /relay error/);
});

// --- settings ---------------------------------------------------------------

test("RATE_PER_HOUR of 0 closes uploads but not pickups", async () => {
  const { call, send, env } = harness({ RATE_PER_HOUR: "20" });
  const { id } = await (await send(new Uint8Array([1]))).json();

  env.RATE_PER_HOUR = "0"; // what a redeploy would do

  assert.equal((await send(new Uint8Array([2]))).status, 429, "uploads closed");
  assert.equal((await call(`/v1/${id}`)).status, 200, "pickups still work");
  assert.equal((await call(`/v1/${id}/done`, { method: "POST" })).status, 200);
});

test("a blank or junk setting falls back instead of meaning zero", async () => {
  for (const bad of ["", "   ", "abc", "-5"]) {
    const { send } = harness({ TTL_SECONDS: bad });
    const response = await send(new Uint8Array([1]));
    assert.equal(response.status, 200, `TTL_SECONDS=${JSON.stringify(bad)}`);
    assert.equal((await response.json()).keeps, "5 hours", "fell back to default");
  }
});
