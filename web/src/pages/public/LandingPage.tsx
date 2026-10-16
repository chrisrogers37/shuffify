import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useSession } from "../../auth/SessionProvider";
import { Button } from "../../components/ui/controls";
import { ShuffleDemo } from "./ShuffleDemo";
import { Example, Flow, MiniPlaylist, PlaylistRow, Showcase } from "./showcase";
function ConnectForm() {
  const [consent, setConsent] = useState(false),
    [enhanced, setEnhanced] = useState(false);
  const checkbox = useRef<HTMLInputElement>(null);
  useEffect(() => {
    setEnhanced(true);
    const update = () => setConsent(checkbox.current?.checked ?? false);
    update();
    window.addEventListener("pageshow", update);
    return () => window.removeEventListener("pageshow", update);
  }, []);
  return (
    <form
      action="/login"
      method="get"
      className="sh-landing-connect"
      data-navigation
    >
      <label htmlFor="legal-consent" className="sh-landing-consent">
        <input
          ref={checkbox}
          type="checkbox"
          id="legal-consent"
          name="legal_consent"
          value="1"
          required
          checked={consent}
          onChange={(event) => setConsent(event.currentTarget.checked)}
        />
        <span>
          I agree to the{" "}
          <a href="/terms" target="_blank" rel="noopener noreferrer">
            Terms of Service
          </a>{" "}
          and{" "}
          <a href="/privacy" target="_blank" rel="noopener noreferrer">
            Privacy Policy
          </a>
          .
        </span>
      </label>
      <Button
        type="submit"
        id="login-button"
        className="sh-landing-connect-button"
        aria-label="Connect with Spotify to start shuffling playlists"
        disabled={enhanced && !consent}
      >
        Connect with Spotify{" "}
        <svg className="sh-landing-icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 12h16m-6-6 6 6-6 6" />
        </svg>
      </Button>
      <p className="sh-landing-caption" id="cta-subtext">
        {consent ? "Free · Ready to Start" : "Free · Approve & Connect"}
      </p>
    </form>
  );
}
export function LandingPage() {
  const session = useSession(),
    navigate = useNavigate();
  useEffect(() => {
    if (session.status === "ready" && session.session.signed_in)
      void navigate("/library", { replace: true });
  }, [session, navigate]);
  return (
    <div className="sh-landing">
      <section
        className="sh-container sh-landing-hero"
        aria-labelledby="hero-title"
      >
        <div className="sh-landing-copy">
          <p className="sh-eyebrow">
            <span aria-hidden="true" />
            Playlist tools for Spotify
          </p>
          <h1 id="hero-title" tabIndex={-1}>
            Evolve your
            <br />
            <span>collection.</span>
          </h1>
          <p className="sh-landing-description">
            Find new songs, keep favorites in rotation, and choose how your
            playlists refresh on your schedule.
          </p>
          <ConnectForm />
          <p className="sh-landing-data-note">
            Sign in securely through Spotify. Shuffify stores settings and
            playlist information to power snapshots, scheduling, and undo.
          </p>
        </div>
        <ShuffleDemo />
      </section>
      <nav
        className="sh-container sh-landing-jump-nav"
        aria-label="Explore playlist tools"
      >
        <p className="sh-landing-caption">More ways to update your playlists</p>
        <a className="sh-button-secondary sh-landing-jump-link" href="#raid">
          Find new songs <span aria-hidden="true">↓</span>
        </a>
        <a
          className="sh-button-secondary sh-landing-jump-link"
          href="#drip-rotation"
        >
          Add new music <span aria-hidden="true">↓</span>
        </a>
        <a
          className="sh-button-secondary sh-landing-jump-link"
          href="#schedules"
        >
          Schedule updates <span aria-hidden="true">↓</span>
        </a>
        <a
          className="sh-button-secondary sh-landing-jump-link"
          href="#hero-title"
        >
          Connect <span aria-hidden="true">↑</span>
        </a>
      </nav>
      <Showcase
        id="raid"
        eyebrow="Find new songs · Raid"
        title="New songs from playlists you trust."
        description="Watch Spotify playlists from friends and curators. Raid copies new songs into a playlist you choose, ready to listen to or add automatically to your everyday mix with Drip."
        steps={[
          [
            "Watch playlists you trust",
            "Choose playlists from friends and curators you turn to for new music.",
          ],
          [
            "Collect new finds on your schedule",
            "Choose a playlist to collect the songs in, then set how often Raid runs.",
          ],
          [
            "Listen or add automatically",
            "Listen to your finds in Spotify, or schedule Raid + Drip to move a few into your everyday playlist.",
          ],
        ]}
      >
        <Example
          title="Watch playlists. Raid new songs."
          caption="Listen to New finds in Spotify, or schedule Raid + Drip to add songs to Everyday mix."
          label="Raid"
        >
          <div className="sh-example-group">
            <p className="sh-example-label">External playlists you watch</p>
            <ul className="sh-example-list">
              <PlaylistRow
                name="After Dark Discoveries"
                detail="Songs from a trusted curator"
                metric="2 new songs"
              />
              <PlaylistRow
                name="Midnight Electronics"
                detail="More songs to explore"
                metric="2 new songs"
              />
            </ul>
          </div>
          <Flow>Raid copies 4 new songs</Flow>
          <div className="sh-example-group">
            <p className="sh-example-label">Playlist you collect into</p>
            <ul className="sh-example-list">
              <PlaylistRow
                name="New finds"
                detail="Velvet Signals · Blue Static · Night Window · Daylight Echo"
                metric="4 new songs"
              />
            </ul>
            <p className="sh-example-note">
              Raid skips songs already in your everyday mix or archive, and
              songs you've dismissed.
            </p>
          </div>
        </Example>
      </Showcase>
      <Showcase
        id="drip-rotation"
        eyebrow="Add new music · Drip"
        title="Keep fresh music coming."
        description="Drip moves a few random songs from another playlist to the top of your everyday mix. Pair it with Raid to collect new finds and keep your playlist refreshed."
        steps={[
          [
            "Choose how many songs to add",
            "Set the number of songs to move each time.",
          ],
          [
            "Keep your existing mix",
            "Your existing songs stay in order below the new additions.",
          ],
        ]}
        numbered={false}
      >
        <Example
          title="Add a few new songs at the top"
          caption="Use your own playlist of finds or let Raid collect them for you. Drip moves songs out of New finds."
          label="Drip"
        >
          <div className="sh-playlist-stage">
            <p className="sh-playlist-stage-title">From New finds</p>
            <Flow>Drip moves 3 random songs to the top</Flow>
            <MiniPlaylist />
          </div>
        </Example>
      </Showcase>
      <Showcase
        id="rotation"
        eyebrow="Revisit your archive · Rotate"
        title="Get old favorites back in the mix."
        description="Swap songs between your everyday playlist and your archive, a second Spotify playlist for music saved for later. Keep more of your collection in the mix."
        steps={[
          [
            "Bring saved favorites back",
            "Saved songs return while some current songs are set aside for another day.",
          ],
          [
            "Keep favorites in place",
            "Protect your opening songs and lock individual favorites so they stay in your everyday playlist. Set a playlist size limit, and Rotate moves excess songs into your archive while keeping protected favorites.",
          ],
        ]}
        numbered={false}
      >
        <Example
          title="Bring saved music back into your mix."
          caption="Locked favorites stay in place."
          label="Rotate"
        >
          <ul
            className="sh-example-list"
            role="list"
            aria-label="Two directions of one Rotate exchange"
          >
            <PlaylistRow
              name="Your archive → Everyday mix"
              detail="2 songs come back."
            />
            <PlaylistRow
              name="Everyday mix → Your archive"
              detail="2 songs are set aside for another day."
            />
          </ul>
        </Example>
      </Showcase>
      <Showcase
        id="schedules"
        eyebrow="Schedule updates"
        title="Make playlist updates automatic."
        description="Give your playlists a rhythm of their own. Pair Raid with Drip to bring in new music, schedule Rotate to revisit old favorites, and use Shuffle to change the order. Choose when each runs and how often."
        steps={[
          [
            "Pair the right operations",
            "Raid + Drip collects songs then adds a few. Raid + Shuffle collects songs then reorders the playlist.",
          ],
          [
            "Choose your pace",
            "Run routines daily, weekly, or every few hours. Set a time for daily and weekly routines.",
          ],
          [
            "Stay in control",
            "Pause a routine or check what changed after each run.",
          ],
        ]}
      >
        <Example
          title="Everyday mix, on your schedule."
          caption="Each routine has its own timing. Raid runs before Drip in the paired routine."
        >
          <ul className="sh-example-list">
            <PlaylistRow
              name="Discover daily"
              detail="Everyday mix · Daily · Raid + Drip"
              metric="Active"
            />
            <PlaylistRow
              name="Revisit weekly"
              detail="Everyday mix · Weekly · Rotate"
              metric="Active"
            />
            <PlaylistRow
              name="Reorder regularly"
              detail="Everyday mix · Every 12 hours · Shuffle"
              metric="Paused"
            />
          </ul>
          <p className="sh-example-note">
            Pause one routine without changing the others.
          </p>
        </Example>
      </Showcase>
      <section
        className="sh-container sh-landing-section"
        aria-labelledby="story-title"
      >
        <div className="sh-landing-section-heading">
          <p className="sh-eyebrow">
            <span aria-hidden="true" />
            Made by a playlist person
          </p>
          <h2 id="story-title">Why I built this.</h2>
          <p>
            Built to keep large playlists fresh, make room for new finds, and
            bring old favorites back.
          </p>
        </div>
      </section>
    </div>
  );
}
