'use client';

import { useEffect, useRef, useState } from 'react';

// Only ever call mermaid.initialize() once per page load, however many
// diagrams end up rendering - not per-mount.
let mermaidInitialized = false;

export default function MermaidDiagram({ chart }: { chart: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const render = async () => {
      try {
        // Dynamic import, not a top-level static one: mermaid touches
        // browser-only APIs at load time, and Next.js still runs a
        // server-render pass for 'use client' components on first
        // load - a static import can break that pass. Importing
        // inside useEffect guarantees this only ever loads after
        // mount, in the browser.
        const mermaid = (await import('mermaid')).default;
        if (!mermaidInitialized) {
          mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'strict' });
          mermaidInitialized = true;
        }
        // A fresh id per render avoids collisions if more than one
        // diagram ever ends up on the same page at once.
        const id = `mermaid-${Math.random().toString(36).slice(2)}`;
        const { svg } = await mermaid.render(id, chart);
        if (!cancelled && containerRef.current) {
          containerRef.current.innerHTML = svg;
          setError(null);
        }
      } catch (err) {
        // The diagram string is AI-generated - same class of risk as
        // any other AI output. A malformed one should degrade to a
        // visible error and the raw text, not crash the review panel.
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not render this diagram.');
      }
    };

    void render();
    return () => {
      cancelled = true;
    };
  }, [chart]);

  if (error) {
    return (
      <div className="rounded-lg border border-amber-800 bg-amber-950/40 p-3">
        <p className="text-[11px] text-amber-300">⚠ Could not render the architecture diagram: {error}</p>
        <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-slate-500">{chart}</pre>
      </div>
    );
  }

  return <div ref={containerRef} className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950 p-4" />;
}
