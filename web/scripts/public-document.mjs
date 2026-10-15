const escape = (value) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
export function publicDocument(
  template,
  { title, description, body, path = "/" },
) {
  if (
    /<script(?![^>]*\bsrc=)[^>]*>|<style\b|\s(?:style|on\w+)\s*=/i.test(
      template + body,
    )
  )
    throw Error(
      "Inline script, style or event behavior is forbidden in public HTML.",
    );
  const marker = /<div id="root"><\/div>/;
  if (!marker.test(template))
    throw Error("The empty root mount is missing from the Vite template.");
  if (!/<title>[^<]*<\/title>/.test(template))
    throw Error("The document title is missing.");
  return template
    .replace(
      /<title>[^<]*<\/title>/,
      `<title>${escape(title)}</title>\n    <meta name="description" content="${escape(description)}">`,
    )
    .replace(
      marker,
      `<div id="root" data-prerendered="true" data-prerendered-route="${escape(path)}">${body}</div>`,
    );
}
