// @author lxy

export interface Citation {
  id: string;
  rank: number;
  repository: string;
  virtualPath: string;
  startLine: number;
  endLine: number;
  excerpt: string;
  indexGeneration: string;
  contentSha256: string;
}

export function CitationList({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  const ordered = [...citations].sort((left, right) => left.rank - right.rank);
  return <details className="citation-list">
    <summary>参考源码 {ordered.length}</summary>
    <div className="citation-grid">
      {ordered.map((citation) => <article className="citation-card" key={citation.id}>
        <header><span>{String(citation.rank).padStart(2, "0")}</span>
          <code>{citation.repository} / {citation.virtualPath}:{citation.startLine}–{citation.endLine}</code></header>
        <pre><code>{citation.excerpt}</code></pre>
      </article>)}
    </div>
  </details>;
}
