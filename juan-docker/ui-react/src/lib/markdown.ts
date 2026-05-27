/**
 * Lightweight, XSS-safe Markdown renderer.
 * No external dependencies. Handles code blocks, inline code,
 * bold, italic, links, lists, and line breaks.
 */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function renderInline(text: string): string {
  return (
    text
      // Bold
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      // Italic
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      // Inline code (escape BEFORE other transforms)
      .replace(/`([^`]+)`/g, (_m, code: string) =>
        `<code class="inline-code">${escapeHtml(code)}</code>`
      )
      // Links — validate href to prevent javascript: URIs
      .replace(
        /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer" class="md-link">$1</a>'
      )
  )
}

export function renderMarkdown(raw: string): string {
  const lines = raw.split('\n')
  const out: string[] = []
  let inCode  = false
  let codeLang = ''
  let codeBuf: string[] = []
  let inList  = false

  const flushList = () => {
    if (inList) { out.push('</ul>'); inList = false }
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!

    // ── Code fence ────────────────────────────────────────────────
    if (line.startsWith('```')) {
      if (!inCode) {
        flushList()
        inCode   = true
        codeLang = escapeHtml(line.slice(3).trim())
        codeBuf  = []
      } else {
        const langAttr = codeLang ? ` data-lang="${codeLang}"` : ''
        out.push(
          `<pre class="code-block"${langAttr}><code>${codeBuf.map(escapeHtml).join('\n')}</code></pre>`
        )
        inCode   = false
        codeLang = ''
        codeBuf  = []
      }
      continue
    }

    if (inCode) {
      codeBuf.push(line)
      continue
    }

    // ── Headings ──────────────────────────────────────────────────
    const headMatch = line.match(/^(#{1,3})\s+(.+)$/)
    if (headMatch) {
      flushList()
      const level = headMatch[1]!.length
      const text  = renderInline(escapeHtml(headMatch[2]!))
      out.push(`<h${level} class="md-h${level}">${text}</h${level}>`)
      continue
    }

    // ── Horizontal rule ───────────────────────────────────────────
    if (/^---+$/.test(line.trim())) {
      flushList()
      out.push('<hr class="md-hr"/>')
      continue
    }

    // ── Unordered list ────────────────────────────────────────────
    const listMatch = line.match(/^[-*]\s+(.+)$/)
    if (listMatch) {
      if (!inList) { out.push('<ul class="md-list">'); inList = true }
      out.push(`<li>${renderInline(escapeHtml(listMatch[1]!))}</li>`)
      continue
    }

    // ── Blank line ────────────────────────────────────────────────
    if (line.trim() === '') {
      flushList()
      out.push('<br/>')
      continue
    }

    // ── Paragraph ─────────────────────────────────────────────────
    flushList()
    out.push(`<p class="md-p">${renderInline(escapeHtml(line))}</p>`)
  }

  if (inCode && codeBuf.length > 0) {
    out.push(`<pre class="code-block"><code>${codeBuf.map(escapeHtml).join('\n')}</code></pre>`)
  }
  flushList()

  return out.join('')
}
