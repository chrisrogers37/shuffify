# API contracts

`src/api/contracts.ts` defines the snake_case JSON wire format. The checked-in
Draft 7 schemas in `schemas/` are generated with `npm run schemas:generate`.
Run `npm run schemas:check` after editing a contract; it rejects missing, stale,
and obsolete schema files. Do not edit schemas by hand.

`fixtures/contract-cases.json` indexes valid fixture files and invalid examples.
The frontend and Python backend use the same cases. Fixture names, URLs, tokens,
tracks, and playlists are synthetic. AJV is a development dependency used by the
contract tests with coercion, default insertion, and property removal disabled.

`GET` and `POST /api/settings` return `SettingsContext`. Signed-out session
responses contain `user: null` and omit `csrf_token`. An auth notice is read once
from a fixed message map; the next session read returns `notice: null`.
`triggered_by` is `manual`, `scheduler`, or `null` when the origin was not saved.
The execution detail union preserves observed resource, bookkeeping, and count
facts, and explicitly represents unavailable detail.

The library and picker share `PlaylistSummary`; library rows add visibility,
pinning, hiding, and undo state. Section arrays retain preference order.
`ReturnTarget` carries navigation intent as an enum. Context errors use the
shared `ApiError` envelope, including missing Workshop playlists.

Node 24 is required. Run `npm ci`, `npm run schemas:check`,
`npm run typecheck`, `npm test`, and `npm run build`. The build emits TypeScript
declarations to `dist/`.
