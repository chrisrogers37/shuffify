/** CSS optimizers may shorten 720ms to .72s; both are the same shared token. */
export function motionMilliseconds(value: string): number {
  const match = /^(\d+(?:\.\d+)?|\.\d+)(ms|s)$/.exec(value.trim());
  if (!match) return 0;
  const milliseconds = Number(match[1]) * (match[2] === "s" ? 1000 : 1);
  return Number.isFinite(milliseconds) ? milliseconds : 0;
}
