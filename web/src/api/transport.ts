/** Injectable fetch boundary. Fixture transports must return real Response objects. */
export type Transport = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export const fetchTransport: Transport = (input, init) =>
  globalThis.fetch(input, init);
