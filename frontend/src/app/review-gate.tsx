'use client';

import { FormEvent, useEffect, useState } from 'react';
import { CheckCircle2, ClipboardCheck, RefreshCw, Send, XCircle } from 'lucide-react';

interface Run {
  id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  log_output: string | null;
  error_output: string | null;
  row_count: number | null;
}

interface ReviewRecord {
  id: string;
  actor_id: string;
  action: string;
  comment: string | null;
  created_at: string;
}

interface ReviewPayload {
  pipeline: { id: string; status: string; current_version: number };
  version: {
    id: string;
    number: number;
    code: string;
    previous_code: string | null;
    review_status: string;
    reviewed_at: string | null;
  };
  runs: Run[];
  review_history: ReviewRecord[];
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
const STORAGE_KEY = '9gear-review-gate-context';

function getSavedContext(): { pipelineId: string; actorId: string } {
  if (typeof window === 'undefined') return { pipelineId: '', actorId: '' };
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (!saved) return { pipelineId: '', actorId: '' };
    const context = JSON.parse(saved) as { pipelineId?: string; actorId?: string };
    return { pipelineId: context.pipelineId || '', actorId: context.actorId || '' };
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
    return { pipelineId: '', actorId: '' };
  }
}

function getErrorMessage(payload: unknown): string {
  if (typeof payload === 'object' && payload && 'detail' in payload) {
    return String(payload.detail);
  }
  return 'The request could not be completed.';
}

export default function ReviewGate({ context }: { context?: { pipelineId: string; actorId: string } }) {
  const [pipelineId, setPipelineId] = useState(() => context?.pipelineId || getSavedContext().pipelineId);
  const [actorId, setActorId] = useState(() => context?.actorId || getSavedContext().actorId);
  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [comment, setComment] = useState('');
  const [editedCode, setEditedCode] = useState('');
  const [cronExpression, setCronExpression] = useState('0 2 * * *');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // useState's initializer only runs once, at mount - it can't pick up a
  // context prop that arrives later (e.g. right after ProjectSetup creates
  // a new draft pipeline, well after ReviewGate already mounted with no
  // context at all). Re-sync whenever a real context actually shows up.
  useEffect(() => {
    if (context?.pipelineId && context?.actorId) {
      setPipelineId(context.pipelineId);
      setActorId(context.actorId);
    }
  }, [context?.pipelineId, context?.actorId]);

  // Auto-load whenever pipelineId/actorId actually change - covers both
  // the sync above and a page refresh restoring a saved session from
  // localStorage, without needing a manual "Load" click either time.
  useEffect(() => {
    if (pipelineId.trim() && actorId.trim()) {
      void loadReview();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipelineId, actorId]);

  const loadReview = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!pipelineId.trim() || !actorId.trim()) {
      setMessage('Enter both the pipeline ID and reviewer ID.');
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v2/pipelines/${encodeURIComponent(pipelineId.trim())}/review?actor_id=${encodeURIComponent(actorId.trim())}`,
      );
      const payload: unknown = await response.json();
      if (!response.ok) throw new Error(getErrorMessage(payload));
      const result = payload as ReviewPayload;
      setReview(result);
      setEditedCode(result.version.code);
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ pipelineId: pipelineId.trim(), actorId: actorId.trim() }));
    } catch (error) {
      setReview(null);
      setMessage(error instanceof Error ? error.message : 'Unable to load review details.');
    } finally {
      setBusy(false);
    }
  };

  const submit = async (path: string, body: Record<string, string>) => {
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ actor_id: actorId.trim(), ...body }),
      });
      const payload: unknown = await response.json();
      if (!response.ok) throw new Error(getErrorMessage(payload));
      await loadReview();
      return true;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to complete the request.');
      return false;
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!review) return;
    if (await submit(`/api/v2/pipeline-versions/${review.version.id}/approve`, { comment })) {
      setComment('');
      setMessage('Version approved. You can now create its schedule.');
    }
  };

  const reject = async () => {
    if (!review) return;
    if (await submit(`/api/v2/pipeline-versions/${review.version.id}/reject`, { comment })) {
      setComment('');
      setMessage('Version rejected. Create an edited version before testing again.');
    }
  };

  const saveEdit = async () => {
    if (!review || !editedCode.trim()) return;
    if (await submit(`/api/v2/pipelines/${review.pipeline.id}/versions`, { generated_code: editedCode })) {
      setMessage('New draft created. It must pass a sandbox test before approval.');
    }
  };

  const schedule = async () => {
    if (!review) return;
    if (await submit(`/api/v2/pipelines/${review.pipeline.id}/schedule`, { cron_expression: cronExpression })) {
      setMessage(`Version scheduled with ${cronExpression}.`);
    }
  };

  const generateAndTest = async () => {
    if (!review) return;
    if (await submit(`/api/v2/pipelines/${review.pipeline.id}/generate`, { max_retries: '3' })) {
      setMessage('Generation and sandbox test completed. Review the resulting version below.');
    }
  };

  const canReview = review?.version.review_status === 'pending_review';
  const canSchedule = review?.version.review_status === 'approved';

  return (
    <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2"><ClipboardCheck className="h-5 w-5 text-violet-400" /> Review &amp; approval gate</h2>
          <p className="mt-1 text-xs text-slate-400">Only a successfully sandbox-tested version can be approved or scheduled.</p>
        </div>
        {review && <span className="rounded-full border border-violet-800 bg-violet-950 px-2.5 py-1 text-xs font-semibold text-violet-300">v{review.version.number} · {review.version.review_status.replace('_', ' ')}</span>}
      </div>

      <form onSubmit={loadReview} className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
        <input value={pipelineId} onChange={(event) => setPipelineId(event.target.value)} placeholder="Pipeline UUID" className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-violet-500 focus:outline-none" />
        <input value={actorId} onChange={(event) => setActorId(event.target.value)} placeholder="Reviewer UUID (temporary)" className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-violet-500 focus:outline-none" />
        <button type="submit" disabled={busy} className="inline-flex items-center justify-center gap-2 rounded-lg border border-violet-700 bg-violet-950 px-4 py-2 text-xs font-semibold text-violet-200 hover:bg-violet-900 disabled:opacity-50"><RefreshCw className={`h-3.5 w-3.5 ${busy ? 'animate-spin' : ''}`} /> Load</button>
      </form>

      {message && <p className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-300">{message}</p>}

      {review && <div className="space-y-4 border-t border-slate-800 pt-5">
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-lg border border-slate-800 bg-slate-950 p-4">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Current version</p>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap font-mono text-xs text-emerald-300">{review.version.code}</pre>
          </div>
          <div className="rounded-lg border border-slate-800 bg-slate-950 p-4">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Previous version</p>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap font-mono text-xs text-slate-400">{review.version.previous_code || 'No prior version.'}</pre>
          </div>
        </div>

        {(review.version.review_status === 'draft' || review.version.review_status === 'testing') && <button onClick={generateAndTest} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg bg-cyan-600 px-3 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50"><Send className="h-3.5 w-3.5" /> {review.version.review_status === 'testing' ? 'Retry generate & sandbox test' : 'Generate & sandbox test'}</button>}

        <div className="rounded-lg border border-slate-800 bg-slate-950 p-4">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">Sandbox evidence</p>
          {review.runs.length === 0 ? <p className="text-xs text-amber-400">No sandbox run is recorded for this version.</p> : review.runs.map((run) => <div key={run.id} className="border-t border-slate-800 py-3 first:border-t-0 first:pt-0"><p className="text-xs font-semibold text-slate-200">{run.status} {run.row_count !== null ? `· ${run.row_count} rows` : ''}</p><pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-xs text-slate-400">{run.log_output || run.error_output || 'No output recorded.'}</pre></div>)}
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-2">
            <label className="block text-xs font-semibold text-slate-300">Review comment</label>
            <textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Why is this safe to promote, or why is it rejected?" className="h-24 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 text-xs text-slate-100 placeholder:text-slate-500 focus:border-violet-500 focus:outline-none" />
            <div className="flex flex-wrap gap-2">
              <button onClick={approve} disabled={!canReview || busy} className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-slate-700"><CheckCircle2 className="h-3.5 w-3.5" /> Approve version</button>
              <button onClick={reject} disabled={!canReview || busy} className="inline-flex items-center gap-1.5 rounded-lg border border-rose-800 bg-rose-950 px-3 py-2 text-xs font-semibold text-rose-300 hover:bg-rose-900 disabled:cursor-not-allowed disabled:opacity-50"><XCircle className="h-3.5 w-3.5" /> Reject</button>
            </div>
          </div>
          <div className="space-y-2">
            <label className="block text-xs font-semibold text-slate-300">Edit code (creates a new draft)</label>
            <textarea value={editedCode} onChange={(event) => setEditedCode(event.target.value)} className="h-24 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 font-mono text-xs text-emerald-300 focus:border-violet-500 focus:outline-none" />
            <button onClick={saveEdit} disabled={busy || !editedCode.trim() || editedCode === review.version.code} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"><Send className="h-3.5 w-3.5" /> Save as new version</button>
          </div>
        </div>

        <div className="flex flex-col gap-2 rounded-lg border border-slate-800 bg-slate-950 p-4 sm:flex-row sm:items-end">
          <label className="flex-1 text-xs font-semibold text-slate-300">Approved schedule (five-field cron)<input value={cronExpression} onChange={(event) => setCronExpression(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 focus:border-violet-500 focus:outline-none" /></label>
          <button onClick={schedule} disabled={!canSchedule || busy} className="rounded-lg bg-violet-600 px-3 py-2 text-xs font-semibold text-white hover:bg-violet-500 disabled:cursor-not-allowed disabled:bg-slate-700">Schedule approved version</button>
        </div>
      </div>}
    </section>
  );
}
