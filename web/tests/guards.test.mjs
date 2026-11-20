import test from "node:test";
import assert from "node:assert/strict";
import { inspect } from "node:util";
import { count, digest, id, record, trackURI } from "../src/api/guards.ts";

const hex = "0123456789abcdef".repeat(4);
const track = (body) => `spotify:track:${body}`;
const base62 = "0123456789abcdefABCDEF";

test("record accepts plain objects only", () => {
  for (const value of [{}, { id: 1 }])
    assert.equal(record(value), true, inspect(value));
  for (const value of [null, undefined, [], [{}], "{}", 1, true])
    assert.equal(record(value), false, inspect(value));
});

test("id accepts positive safe integers only", () => {
  for (const value of [1, Number.MAX_SAFE_INTEGER])
    assert.equal(id(value), true, inspect(value));
  for (const value of [
    0,
    -1,
    1.5,
    Number.MAX_SAFE_INTEGER + 1,
    NaN,
    Infinity,
    "1",
    1n,
    null,
  ])
    assert.equal(id(value), false, inspect(value));
});

test("count accepts zero and positive safe integers only", () => {
  for (const value of [0, 1, Number.MAX_SAFE_INTEGER])
    assert.equal(count(value), true, inspect(value));
  for (const value of [-1, 1.5, Number.MAX_SAFE_INTEGER + 1, NaN, "0", null])
    assert.equal(count(value), false, inspect(value));
});

test("digest accepts 64 lowercase hex characters only", () => {
  assert.equal(digest(hex), true);
  for (const value of [
    hex.toUpperCase(),
    hex.slice(1),
    hex + "0",
    hex.slice(1) + "g",
    hex + "\n",
    [hex],
    null,
  ])
    assert.equal(digest(value), false, inspect(value));
});

test("trackURI accepts a Spotify track URI with a 22-character ID only", () => {
  assert.equal(trackURI(track(base62)), true);
  for (const value of [
    track(base62.slice(1)),
    track(base62 + "x"),
    track(base62.slice(1) + "_"),
    `spotify:album:${base62}`,
    track(base62) + "\n",
    [track(base62)],
    null,
  ])
    assert.equal(trackURI(value), false, inspect(value));
});
