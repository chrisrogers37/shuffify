import { test } from "node:test";
import assert from "node:assert/strict";
import {
  prepareTasks,
  removeArchiveCopy,
  cancelArchiveCopies,
  confirmedDismissal,
  sourceResult,
} from "../src/pages/workshop/sources/model.ts";
import { uri } from "./workshop-data.mjs";
const staging = [
  {
    kind: "raid_promotion",
    source_playlist_id: "shared",
    pending_rows: [{ id: 7, track_uri: uri(1) }],
  },
  {
    kind: "archive_restore",
    source_playlist_id: "shared",
    track_uris: [uri(2)],
  },
  {
    kind: "archive_append",
    source_playlist_id: "shared",
    track_uris: [uri(3), uri(3)],
    baseline_track_uris: [],
  },
];
test("source review freezes exact IDs, full duplicate order and same-resource removal dependencies", () => {
  let key = 0;
  const work = prepareTasks(
    staging,
    [uri(1), uri(2)],
    {
      target: { id: "target", name: "Target" },
      raid: { id: "shared", name: "Source" },
      archive: {
        id: "shared",
        name: "Archive",
        baseline_track_uris: [uri(1), uri(1), uri(2), uri(4)],
      },
    },
    () => `key-${++key}`,
  );
  assert.deepEqual(work.tasks[0].body.track_ids, [7]);
  assert.deepEqual(work.tasks[2].body.baseline_track_uris, [uri(4)]);
  assert.deepEqual(
    work.tasks[2].dependsOn,
    work.tasks.slice(0, 2).map((x) => x.id),
  );
  assert.deepEqual(work.tasks[2].body.track_uris, [uri(3), uri(3)]);
  assert.ok(Object.isFrozen(work.tasks[2].body.track_uris));
});
test("source review refuses a missing staged URI and never silently follows replacement binding", () => {
  const context = {
    target: { id: "target", name: "Target" },
    raid: { id: "replacement", name: "Replacement" },
    archive: null,
  };
  assert.throws(
    () => prepareTasks(staging.slice(0, 1), [uri(1)], context),
    /changed|original/i,
  );
  assert.throws(
    () =>
      prepareTasks(staging.slice(0, 1), [], {
        ...context,
        raid: { id: "shared", name: "Source" },
      }),
    /missing/i,
  );
});
test("archive queue counts saved removed occurrences and append-back cancels only excess copies", () => {
  let queue = removeArchiveCopy([], uri(1), 1, 2);
  queue = removeArchiveCopy(queue, uri(1), 0, 2);
  assert.deepEqual(queue, [uri(1), uri(1)]);
  assert.deepEqual(removeArchiveCopy(queue, uri(1), 0, 2), queue);
  assert.deepEqual(cancelArchiveCopies(queue, uri(1), 1, 2), [uri(1)]);
  assert.deepEqual(removeArchiveCopy(queue, uri(2), 0, 0), queue);
});
test("dismissal consumes exact proven IDs even on cleanup failure; unknown receipt never guesses requested identities", () => {
  assert.deepEqual(
    confirmedDismissal(
      {
        database_status: "complete",
        dismissed_ids: [2, 4],
        dismissed_count: 2,
        source_status: "failed",
      },
      [2, 4, 6],
    ),
    [2, 4],
  );
  assert.equal(
    confirmedDismissal(
      {
        database_status: "unknown",
        dismissed_ids: null,
        dismissed_count: null,
      },
      [2],
    ),
    null,
  );
  assert.throws(
    () =>
      confirmedDismissal(
        {
          database_status: "complete",
          dismissed_ids: [99],
          dismissed_count: 1,
        },
        [2],
      ),
    /identity/i,
  );
});
test("inconclusive inspection never upgrades saved source failure or accepts success without exact resource", () => {
  const task = { source: { id: "source", name: "Source" } };
  assert.equal(
    sourceResult(task, { success: true, resource: "other", status: "complete" })
      .status,
    "unknown",
  );
  assert.equal(
    sourceResult(task, {
      success: false,
      resource: "source",
      status: "unknown",
      source_status: "complete",
      database_status: "pending",
      error_code: "database_update_pending",
      retryable: true,
    }).retryable,
    false,
  );
  assert.equal(
    sourceResult(task, {
      success: false,
      resource: "source",
      status: "unknown",
      retryable: true,
    }).retryable,
    false,
  );
  assert.equal(
    sourceResult(task, {
      success: false,
      resource: "source",
      status: "failed",
      source_status: "unknown",
      retryable: true,
    }).retryable,
    false,
  );
});
test("explicit unknown source or database facts cannot be projected as a completed source task", () => {
  const task = { source: { id: "source", name: "Source" } };
  for (const facts of [
    { source_status: "unknown" },
    { database_status: "unknown" },
    { database_status: "failed" },
  ])
    assert.equal(
      sourceResult(task, {
        success: true,
        resource: "source",
        status: "complete",
        ...facts,
      }).status,
      "unknown",
    );
});
