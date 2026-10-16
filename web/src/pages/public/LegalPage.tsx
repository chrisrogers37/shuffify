import { Link } from "react-router";
import { legalDocuments } from "./generated/legal";
export function LegalPage({ document }: { document: "terms" | "privacy" }) {
  const { title, html } = legalDocuments[document];
  return (
    <div className="sh-container legal-page">
      <article className="legal-document" aria-labelledby="legal-title">
        <header className="legal-heading">
          <h1 id="legal-title" tabIndex={-1}>
            {title}
          </h1>
        </header>
        <div dangerouslySetInnerHTML={{ __html: html }} />
        <div className="legal-back">
          <Link to="/" className="sh-button-secondary">
            ← Back to Shuffify
          </Link>
        </div>
      </article>
    </div>
  );
}
