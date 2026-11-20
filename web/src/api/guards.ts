// The checks response parsers share. Each rule is written here once.
export const record = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);
export const id = (v: unknown): v is number =>
  Number.isSafeInteger(v) && (v as number) > 0;
export const count = (v: unknown): v is number =>
  Number.isSafeInteger(v) && (v as number) >= 0;
export const digest = (v: unknown): v is string =>
  typeof v === "string" && /^[0-9a-f]{64}$/u.test(v);
export const trackURI = (v: unknown): v is string =>
  typeof v === "string" && /^spotify:track:[A-Za-z0-9]{22}$/u.test(v);
// An inclusive [minimum, maximum] count, as the server sends it.
export type CountRange = [number, number];
export const countRange = (v: unknown): v is CountRange =>
  Array.isArray(v) && v.length === 2 && v.every(count) && v[0] <= v[1];
export const inRange = (v: unknown, [low, high]: CountRange) =>
  count(v) && low <= v && v <= high;
