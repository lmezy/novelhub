export interface SnippetSegment {
  text: string
  hit: boolean
}

/**
 * Split a search snippet into plain and matched segments.
 *
 * The API anchors a served snippet at the match (see ``SNIPPET_LEAD_CHARS`` in
 * ``app/services/search.py``), so this normally marks the very first characters:
 * the result list clamps a snippet to two lines, and a phone line holds about a
 * third of a desktop line's glyphs, which is why the searched word is worth
 * marking instead of leaving the reader to spot it in a wall of text.
 *
 * Terms are matched literally and case-insensitively, longest first; a result is
 * always a usable segment list, so callers can render it unconditionally.
 */
export function splitSnippet(text: string, terms: string[]): SnippetSegment[] {
  const value = text || ""
  if (!value) return []

  const needles = Array.from(
    new Set(
      (terms || [])
        .map((term) => (term || "").trim().toLowerCase())
        .filter((term) => term.length > 0),
    ),
  ).sort((a, b) => b.length - a.length)
  if (needles.length === 0) return [{ text: value, hit: false }]

  const lowered = value.toLowerCase()
  const segments: SnippetSegment[] = []
  let cursor = 0
  while (cursor < value.length) {
    let at = -1
    let length = 0
    for (const needle of needles) {
      const found = lowered.indexOf(needle, cursor)
      if (found === -1) continue
      if (at === -1 || found < at || (found === at && needle.length > length)) {
        at = found
        length = needle.length
      }
    }
    if (at === -1) break
    if (at > cursor) segments.push({ text: value.slice(cursor, at), hit: false })
    segments.push({ text: value.slice(at, at + length), hit: true })
    cursor = at + length
  }
  if (cursor < value.length) segments.push({ text: value.slice(cursor), hit: false })
  return segments.length ? segments : [{ text: value, hit: false }]
}
