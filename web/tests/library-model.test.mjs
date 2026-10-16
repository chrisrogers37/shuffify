import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  createShuffleDraft,
  selectMethod,
  setParameter,
  shuffleForm,
  validateShuffle,
  placeRow,
  moveRow,
  orderedIds,
  greeting,
} from "../src/pages/library/model.ts";
const settings = JSON.parse(
  await readFile(
    new URL("../fixtures/settings/initial.json", import.meta.url),
    "utf8",
  ),
);
const algorithms = settings.algorithms;
const row = (id, extra = {}) => ({
  id,
  name: id,
  image_url: null,
  track_count: 3,
  spotify_url: `https://open.spotify.com/playlist/${id}`,
  public: false,
  pinned: false,
  hidden: false,
  can_undo: false,
  ...extra,
});
test("server defaults are validated, keep_first is capped by track count, unknown method stays unselected", () => {
  const saved = { algorithm: "BasicShuffle", params: { keep_first: 20 } };
  const draft = createShuffleDraft(row("alice"), algorithms, saved);
  assert.equal(draft.algorithm, "BasicShuffle");
  assert.equal(draft.values.BasicShuffle.keep_first, "3");
  assert.equal(
    createShuffleDraft(row("alice"), algorithms, {
      algorithm: "Missing",
      params: {},
    }).algorithm,
    "",
  );
  const invalid = createShuffleDraft(row("alice"), algorithms, {
    algorithm: "BasicShuffle",
    params: { keep_first: -2 },
  });
  assert.equal(invalid.values.BasicShuffle.keep_first, "0");
});
test("method changes cache exact user edits without normalizing invalid drafts or sending writes", () => {
  let draft = createShuffleDraft(row("alice"), algorithms, {
    algorithm: null,
    params: {},
  });
  draft = selectMethod(draft, "BasicShuffle", row("alice"), algorithms, {
    algorithm: null,
    params: {},
  });
  draft = setParameter(draft, "keep_first", "2");
  const original = draft.revision;
  draft = selectMethod(draft, "PercentageShuffle", row("alice"), algorithms, {
    algorithm: null,
    params: {},
  });
  draft = setParameter(draft, "shuffle_percentage", "");
  draft = selectMethod(draft, "BasicShuffle", row("alice"), algorithms, {
    algorithm: null,
    params: {},
  });
  assert.equal(draft.values.BasicShuffle.keep_first, "2");
  assert.ok(draft.revision > original);
  draft = selectMethod(draft, "PercentageShuffle", row("alice"), algorithms, {
    algorithm: null,
    params: {},
  });
  assert.equal(draft.values.PercentageShuffle.shuffle_percentage, "");
});
test("shuffle validation respects metadata, full scalar form and integer/float semantics", () => {
  let draft = createShuffleDraft(row("alice"), algorithms, {
    algorithm: "BasicShuffle",
    params: { keep_first: 1 },
  });
  assert.deepEqual(validateShuffle(draft, row("alice"), algorithms), {});
  assert.deepEqual(shuffleForm(draft, row("alice"), algorithms), {
    algorithm: "BasicShuffle",
    keep_first: "1",
  });
  for (const invalid of ["", "-1", "4", "1.5"]) {
    const changed = setParameter(draft, "keep_first", invalid);
    assert.ok(validateShuffle(changed, row("alice"), algorithms).keep_first);
    assert.throws(
      () => shuffleForm(changed, row("alice"), algorithms),
      TypeError,
    );
  }
  draft = selectMethod(draft, "PercentageShuffle", row("alice"), algorithms, {
    algorithm: null,
    params: {},
  });
  draft = setParameter(draft, "shuffle_percentage", "25.5");
  draft = setParameter(draft, "shuffle_location", "back");
  assert.deepEqual(shuffleForm(draft, row("alice"), algorithms), {
    algorithm: "PercentageShuffle",
    shuffle_percentage: "25.5",
    shuffle_location: "back",
  });
  assert.ok(
    validateShuffle(
      setParameter(draft, "shuffle_location", "elsewhere"),
      row("alice"),
      algorithms,
    ).shuffle_location,
  );
});
test("preference placement matches saved flags and prepends ordinary rows without altering hidden pinned state", () => {
  const a = row("alice"),
    b = row("bob"),
    c = row("claire", { pinned: true }),
    h = row("hidden", { hidden: true, pinned: true });
  const sections = { favorites: [c], playlists: [a, b], hidden: [h] };
  let updated = placeRow(sections, "alice", { pinned: true });
  assert.deepEqual(
    updated.favorites.map((p) => p.id),
    ["claire", "alice"],
  );
  assert.deepEqual(
    updated.playlists.map((p) => p.id),
    ["bob"],
  );
  updated = placeRow(updated, "alice", { pinned: false });
  assert.deepEqual(
    updated.playlists.map((p) => p.id),
    ["alice", "bob"],
  );
  updated = placeRow(updated, "hidden", { pinned: false });
  assert.equal(updated.hidden[0].hidden, true);
  assert.equal(updated.hidden[0].pinned, false);
  updated = placeRow(updated, "hidden", { hidden: false });
  assert.deepEqual(
    updated.playlists.map((p) => p.id),
    ["hidden", "alice", "bob"],
  );
  assert.deepEqual(
    sections.playlists.map((p) => p.id),
    ["alice", "bob"],
  );
});
test("moves stay in one ordered section, clamp/truncate positions, and save Favorites then ordinary IDs only", () => {
  const sections = {
    favorites: [row("fav", { pinned: true })],
    playlists: [row("alice"), row("bob"), row("claire")],
    hidden: [row("hidden", { hidden: true })],
  };
  let updated = moveRow(sections, "alice", 3.9);
  assert.deepEqual(orderedIds(updated), ["fav", "bob", "claire", "alice"]);
  updated = moveRow(updated, "alice", -10);
  assert.deepEqual(orderedIds(updated), ["fav", "alice", "bob", "claire"]);
  assert.equal(moveRow(updated, "hidden", 1), updated);
  assert.throws(() => moveRow(updated, "alice", NaN), TypeError);
});
test("greeting preserves first visit, returning singular/plural and unnamed accounts", () => {
  const base = {
    is_new_user: false,
    is_returning_user: true,
    display_name: "Alice",
    activity_since_last_login_count: 1,
  };
  assert.deepEqual(greeting(base), {
    welcome: "Welcome back, Alice!",
    detail: "1 thing happened since your last visit.",
    onboarding: false,
  });
  assert.equal(
    greeting({ ...base, activity_since_last_login_count: 0 }).detail,
    "Everything is up to date.",
  );
  assert.equal(
    greeting({ ...base, display_name: null, is_returning_user: false }).welcome,
    "Welcome to Shuffify!",
  );
});

test("Library scenario corpus satisfies the unchanged generated schema without defaults or coercion", async () => {
  const { default: Ajv } = await import("ajv");
  const schema = JSON.parse(
    await readFile(
      new URL("../schemas/library-context.schema.json", import.meta.url),
      "utf8",
    ),
  );
  const value = JSON.parse(
    await readFile(
      new URL("../fixtures/library/initial.json", import.meta.url),
      "utf8",
    ),
  );
  const ajv = new Ajv({
    strict: true,
    allErrors: true,
    useDefaults: false,
    coerceTypes: false,
  });
  const validate = ajv.compile(schema);
  assert.equal(validate(value), true, JSON.stringify(validate.errors));
  assert.equal(
    validate({
      ...value,
      sections: { favorites: [], playlists: [], hidden: [] },
    }),
    true,
    JSON.stringify(validate.errors),
  );
});
