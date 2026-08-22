'use client';

import { useState } from 'react';
import ProjectSetup from '../project-setup';
import ReviewGate from '../review-gate';

export default function SetupPage() {
  const [pipelineContext, setPipelineContext] = useState<
    { pipelineId: string; actorId: string } | undefined
  >(undefined);

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 p-8 font-sans">
      <div className="max-w-5xl mx-auto space-y-8">
        <header className="border-b border-slate-800 pb-6">
          <h1 className="text-2xl font-bold tracking-tight">9Gear Pulse — Review Gate</h1>
          <p className="text-xs text-slate-400 mt-1">
            Set up a project and connections, then generate, sandbox-test, review, and schedule a pipeline.
          </p>
        </header>

        <ProjectSetup
          onPipelineCreated={(pipelineId, actorId) => setPipelineContext({ pipelineId, actorId })}
        />

        <ReviewGate context={pipelineContext} />
      </div>
    </main>
  );
}
