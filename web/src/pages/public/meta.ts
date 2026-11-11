export const publicPages = {
  "/": {
    title: "Raccoon: Evolve your collection.",
    description:
      "Find new songs, keep favorites in rotation, and choose how your playlists refresh on your schedule.",
  },
  "/terms": {
    title: "Terms of Service - Raccoon",
    description:
      "Terms of Service for Raccoon, the independent Spotify playlist management app.",
  },
  "/privacy": {
    title: "Privacy Policy - Raccoon",
    description:
      "How Raccoon collects, uses, stores and protects your information.",
  },
} as const;
export type PublicPath = keyof typeof publicPages;
export function publicPage(path: string) {
  const normalized = path.replace(/\/$/, "") || "/";
  return Object.hasOwn(publicPages, normalized)
    ? publicPages[normalized as PublicPath]
    : undefined;
}
export const copyrightYear = 2026;
