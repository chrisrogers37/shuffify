import { Link } from "react-router";
const messages = {
  404: [
    "That page could not be found.",
    "Check the address or return to Shuffify.",
  ],
  500: ["Something went wrong.", "Please try again in a moment."],
  503: ["Shuffify is temporarily unavailable.", "Please check back shortly."],
} as const;
export function ErrorPage({ code = 404 }: { code?: 404 | 500 | 503 }) {
  const [title, message] = messages[code];
  return (
    <div className="sh-container sh-foundation-page">
      <section className="sh-foundation-error">
        <p className="sh-eyebrow">{code}</p>
        <h1 tabIndex={-1} className="sh-heading">
          {title}
        </h1>
        <p>{message}</p>
        <Link className="sh-button" to="/">
          Return to Shuffify
        </Link>
      </section>
    </div>
  );
}
