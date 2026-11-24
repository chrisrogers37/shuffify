import { useState } from "react";
import { useServices } from "../../auth/SessionProvider";
import { Button } from "../../components/ui/controls";

type Review = { version: number; issued_at: number; signature: string };

export function PlaylistHold({ playlistId, refreshHolds }: {
  playlistId: string;
  refreshHolds: () => Promise<void>;
}) {
  const { api } = useServices();
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function submit() {
    setBusy(true);
    try {
      const result = await api.request<{ review: Review | null; message: string }>(
        `/api/playlist/${encodeURIComponent(playlistId)}/operation-review`,
        review ? { method: "POST", json: { review } } : {},
      );
      if (result.status !== "completed") throw new Error("Reload this review.");
      if (review) {
        setReview(null);
        setMessage(result.data.message);
      } else {
        setReview(result.data.review);
        setMessage(result.data.review ? "" : "This playlist has no hold to review.");
      }
      if (review || !result.data.review) {
        try {
          await refreshHolds();
        } catch {
          setMessage(review
            ? "The hold was released. Review again to refresh the Workshop receipt."
            : "This playlist has no hold. Review again to refresh the Workshop receipt.");
        }
      }
    } catch (error) {
      setReview(null);
      setMessage(error instanceof Error ? error.message : "Review could not be loaded.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="Playlist hold">
      {review && (
        <div>
          <p>Review the current playlist before releasing this hold on future operations.</p>
          <a
            className="sh-button-secondary"
            href={`https://open.spotify.com/playlist/${encodeURIComponent(playlistId)}`}
            target="_blank"
            rel="noreferrer"
          >
            Open current playlist on Spotify
          </a>
        </div>
      )}
      <Button variant="secondary" disabled={busy} onClick={() => void submit()}>
        {review ? "Keep current playlist and release hold" : "Review playlist hold"}
      </Button>
      {message && <p role="status">{message}</p>}
    </section>
  );
}
