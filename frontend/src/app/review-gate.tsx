'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useAuth } from '@clerk/nextjs';
import { CheckCircle2, ChevronDown, ChevronRight, ClipboardCheck, Download, RefreshCw, Send, XCircle } from 'lucide-react';
import MermaidDiagram from './mermaid-diagram';

interface QualityChecks {
  checked: boolean;
  reason?: string;
  dataset?: string;
  table?: string;
  row_count?: number;
  warnings?: string[];
}

interface Run {
  id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  log_output: string | null;
  error_output: string | null;
  row_count: number | null;
  quality_checks: QualityChecks | null;
}

interface ReviewRecord {
  id: string;
  actor_id: string;
  action: string;
  comment: string | null;
  created_at: string;
}

interface ProposedFile {
  file_name: string;
  purpose: string;
  reads_from: string[];
  destination_table: string;
  directory: string | null;
}

interface ScaffoldFile {
  path: string;
  purpose: string;
  scaffold_type: string;
  generated: boolean;
}

interface ArchitectureProposal {
  feasible: boolean;
  feasibility_notes: string;
  summary: string;
  source_tables_used: string[];
  destination_dataset: string | null;
  destination_table: string | null;
  approach: string | null;
  key_transformations: string[];
  assumptions: string[];
  files: ProposedFile[];
  architecture_diagram_mermaid: string | null;
  project_structure: ScaffoldFile[];
}

interface VersionFile {
  id: string;
  file_order: number;
  file_name: string;
  purpose: string;
  reads_from: string[];
  destination_table: string;
  generated_code: string | null;
  review_status: string;
  generation_in_progress: boolean;
  runs: Run[];
  // All four null/absent unless this version uses_pinned_schema - see
  // ReviewPayload.version.uses_pinned_schema below, which gates
  // whether the schema-review section renders for this file at all.
  schema_ddl: string | null;
  schema_review_status: string | null;
  schema_applied_at: string | null;
  schema_rejection_comment: string | null;
}

// Distinct from ScaffoldFile above (which is just a plan entry inside
// an architecture proposal, not yet a real row) - this is an actual
// PipelineVersionScaffoldFile once the proposal that named it has
// been approved.
interface VersionScaffoldFile {
  id: string;
  path: string;
  purpose: string;
  scaffold_type: string;
  generated_content: string | null;
  review_status: string;
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
    architecture_proposal: ArchitectureProposal | null;
    architecture_status: string | null;
    generation_in_progress: boolean;
    uses_pinned_schema: boolean;
  };
  runs: Run[];
  review_history: ReviewRecord[];
  files: VersionFile[];
  scaffold_files: VersionScaffoldFile[];
  schedule: { id: string; cron_expression: string; next_run_at: string | null } | null;
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
const STORAGE_KEY = '9gear-review-gate-context';

function getSavedPipelineId(): string {
  if (typeof window === 'undefined') return '';
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (!saved) return '';
    const context = JSON.parse(saved) as { pipelineId?: string };
    return context.pipelineId || '';
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
    return '';
  }
}

function getErrorMessage(payload: unknown): string {
  if (typeof payload === 'object' && payload && 'detail' in payload) {
    return String(payload.detail);
  }
  return 'The request could not be completed.';
}

// context still accepts an actorId field so setup/page.tsx's existing
// wiring doesn't break before it gets its own matching cleanup pass -
// it's just never read here anymore. Auth comes entirely from
// useAuth()'s real session token now.
export default function ReviewGate({ context }: { context?: { pipelineId: string; actorId?: string } }) {
  const { getToken } = useAuth();
  const [pipelineId, setPipelineId] = useState(() => context?.pipelineId || getSavedPipelineId());
  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [comment, setComment] = useState('');
  const [architectureComment, setArchitectureComment] = useState('');
  const [fileComment, setFileComment] = useState('');
  const [schemaComment, setSchemaComment] = useState('');
  const [scaffoldFileComment, setScaffoldFileComment] = useState('');
  const [editedCode, setEditedCode] = useState('');
  const [cronExpression, setCronExpression] = useState('0 2 * * *');
  // Each run's log is collapsed by default and toggles independently -
  // not an accordion where opening one closes another, matching how
  // Airflow's own per-task log view behaves.
  const [expandedRuns, setExpandedRuns] = useState<Set<string>>(new Set());
  const toggleRun = (runId: string) => {
    setExpandedRuns((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });
  };
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // useState's initializer only runs once, at mount - it can't pick up a
  // context prop that arrives later (e.g. right after ProjectSetup creates
  // a new draft pipeline, well after ReviewGate already mounted with no
  // context at all). Re-sync whenever a real context actually shows up.
  useEffect(() => {
    if (context?.pipelineId) {
      setPipelineId(context.pipelineId);
    }
  }, [context?.pipelineId]);

  // Auto-load whenever pipelineId actually changes - covers both the
  // sync above and a page refresh restoring a saved session from
  // localStorage, without needing a manual "Load" click either time.
  useEffect(() => {
    if (pipelineId.trim()) {
      void loadReview();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipelineId]);

  const loadReview = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!pipelineId.trim()) {
      setMessage('Enter the pipeline ID.');
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const token = await getToken();
      if (!token) {
        setMessage('You need to be signed in to load a review.');
        setBusy(false);
        return;
      }
      const response = await fetch(
        `${API_BASE_URL}/api/v2/pipelines/${encodeURIComponent(pipelineId.trim())}/review`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      const payload: unknown = await response.json();
      if (!response.ok) throw new Error(getErrorMessage(payload));
      const result = payload as ReviewPayload;
      setReview(result);
      setEditedCode(result.version.code);
      // Pre-fill with the REAL current schedule when one exists, so
      // "Update schedule" edits the actual value instead of silently
      // offering to overwrite it with the unrelated hardcoded default.
      if (result.schedule) {
        setCronExpression(result.schedule.cron_expression);
      }
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ pipelineId: pipelineId.trim() }));
    } catch (error) {
      setReview(null);
      setMessage(error instanceof Error ? error.message : 'Unable to load review details.');
    } finally {
      setBusy(false);
    }
  };

  // Refetches the review WITHOUT toggling busy or resetting editedCode/
  // cronExpression - a background poll tick shouldn't disable every
  // button on the page for its duration, and it must never clobber
  // something the user is actively typing into an editable field just
  // because an unrelated background generation happened to tick at
  // that moment. Read-only display data (review_status,
  // generation_in_progress, runs) still updates live via setReview.
  const fetchReviewSilently = async (): Promise<ReviewPayload | null> => {
    try {
      const token = await getToken();
      if (!token) return null;
      const response = await fetch(
        `${API_BASE_URL}/api/v2/pipelines/${encodeURIComponent(pipelineId.trim())}/review`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      const payload: unknown = await response.json();
      if (!response.ok) return null;
      const result = payload as ReviewPayload;
      setReview(result);
      return result;
    } catch {
      return null;
    }
  };

  // Generation now runs in a background Celery worker rather than
  // inline in the request - there's no single HTTP response left to
  // tell the UI "this attempt is over", so this polls until the given
  // predicate (checked against a freshly-fetched review) says it's
  // done. Capped at a fixed number of attempts rather than unbounded,
  // so a genuinely stuck worker (crashed, never started) surfaces as a
  // clear message instead of polling silently forever with no way for
  // the user to know anything is wrong.
  const pollUntilDone = async (isDone: (r: ReviewPayload) => boolean, label: string) => {
    const maxAttempts = 60;
    const intervalMs = 3000;
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
      const result = await fetchReviewSilently();
      if (result && isDone(result)) {
        setMessage(`${label} finished. Review the result below.`);
        return;
      }
    }
    setMessage(`${label} is taking longer than expected - it may still be running. Reload to check its latest status.`);
  };

  const submit = async (path: string, body: Record<string, string>) => {
    setBusy(true);
    setMessage(null);
    try {
      const token = await getToken();
      if (!token) {
        setMessage('You need to be signed in to do that.');
        setBusy(false);
        return false;
      }
      const response = await fetch(`${API_BASE_URL}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify(body),
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

  const unschedule = async () => {
    if (!review) return;
    if (!window.confirm('Stop this pipeline from running on its schedule? The pipeline and its review history stay intact.')) return;
    setBusy(true);
    setMessage(null);
    try {
      const token = await getToken();
      if (!token) {
        setMessage('You need to be signed in to do that.');
        setBusy(false);
        return;
      }
      const response = await fetch(`${API_BASE_URL}/api/v2/pipelines/${review.pipeline.id}/schedule`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        const payload: unknown = await response.json();
        throw new Error(getErrorMessage(payload));
      }
      setMessage('Schedule removed. The pipeline is no longer running automatically.');
      await loadReview();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to remove the schedule.');
    } finally {
      setBusy(false);
    }
  };

  const exportPipeline = async () => {
    if (!review) return;
    setBusy(true);
    setMessage(null);
    try {
      const token = await getToken();
      if (!token) {
        setMessage('You need to be signed in to do that.');
        return;
      }
      const response = await fetch(`${API_BASE_URL}/api/v2/pipelines/${review.pipeline.id}/export`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        const payload: unknown = await response.json();
        throw new Error(getErrorMessage(payload));
      }
      const blob = await response.blob();
      // Filename comes from the backend's own Content-Disposition
      // header (project-slug based, .py or .zip depending on whether
      // this version is multi-file) - read it rather than guess, so
      // this never drifts out of sync with what the backend actually
      // decided to name it.
      const disposition = response.headers.get('Content-Disposition') || '';
      const match = disposition.match(/filename="(.+)"/);
      const filename = match ? match[1] : 'pipeline.py';
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to export this pipeline.');
    } finally {
      setBusy(false);
    }
  };

  const generateAndTest = async () => {
    if (!review) return;
    if (await submit(`/api/v2/pipelines/${review.pipeline.id}/generate`, { max_retries: '3' })) {
      setMessage('Generation queued - this can take a while, especially across self-healing retries.');
      void pollUntilDone((r) => !r.version.generation_in_progress, 'Generation');
    }
  };

  const proposeArchitecture = async () => {
    if (!review) return;
    if (await submit(`/api/v2/pipelines/${review.pipeline.id}/propose-architecture`, {})) {
      setMessage('Architecture proposed - review it below before generating code.');
    }
  };

  const approveArchitecture = async () => {
    if (!review) return;
    if (await submit(`/api/v2/pipeline-versions/${review.version.id}/approve-architecture`, { comment: architectureComment })) {
      setArchitectureComment('');
      setMessage('Architecture approved. You can now generate and sandbox-test the code.');
    }
  };

  const rejectArchitecture = async () => {
    if (!review) return;
    if (await submit(`/api/v2/pipeline-versions/${review.version.id}/reject-architecture`, { comment: architectureComment })) {
      setArchitectureComment('');
      setMessage('Architecture rejected. Propose a new one to try again.');
    }
  };

  const generateFile = async (fileId: string) => {
    if (await submit(`/api/v2/pipeline-version-files/${fileId}/generate`, { max_retries: '3' })) {
      setMessage('File generation queued - this can take a while, especially across self-healing retries.');
      void pollUntilDone((r) => {
        const file = r.files.find((f) => f.id === fileId);
        // If the file is gone entirely (deleted mid-generation), stop
        // polling rather than run to the attempt cap for nothing.
        return file ? !file.generation_in_progress : true;
      }, 'File generation');
    }
  };

  const approveFile = async (fileId: string) => {
    if (await submit(`/api/v2/pipeline-version-files/${fileId}/approve`, { comment: fileComment })) {
      setFileComment('');
      setMessage('File approved.');
    }
  };

  const rejectFile = async (fileId: string) => {
    if (await submit(`/api/v2/pipeline-version-files/${fileId}/reject`, { comment: fileComment })) {
      setFileComment('');
      setMessage('File rejected. Regenerate it to try again.');
    }
  };

  const proposeFileSchema = async (fileId: string) => {
    // Synchronous, unlike generateFile - propose-schema is a pure AI
    // call with no sandbox execution at all, so there's nothing to
    // poll for; submit() already reloads the review after it resolves.
    if (await submit(`/api/v2/pipeline-version-files/${fileId}/propose-schema`, {})) {
      setMessage('Schema proposed - review the DDL below before it gets applied to the real database.');
    }
  };

  const approveFileSchema = async (fileId: string) => {
    if (await submit(`/api/v2/pipeline-version-files/${fileId}/approve-schema`, {})) {
      setMessage('Schema approved and applied to the real database. You can now generate this file\'s code.');
    }
  };

  const rejectFileSchema = async (fileId: string) => {
    if (await submit(`/api/v2/pipeline-version-files/${fileId}/reject-schema`, { comment: schemaComment })) {
      setSchemaComment('');
      setMessage('Schema rejected - nothing was applied to the database. Propose a new one to try again.');
    }
  };

  const generateScaffoldFile = async (scaffoldFileId: string) => {
    // No max_retries - the backend endpoint takes no body at all;
    // scaffold generation is one attempt plus type-appropriate
    // validation, not a self-healing sandbox loop.
    if (await submit(`/api/v2/pipeline-version-scaffold-files/${scaffoldFileId}/generate`, {})) {
      setMessage('Scaffold file generated. Review the result below.');
    }
  };

  const approveScaffoldFile = async (scaffoldFileId: string) => {
    if (await submit(`/api/v2/pipeline-version-scaffold-files/${scaffoldFileId}/approve`, { comment: scaffoldFileComment })) {
      setScaffoldFileComment('');
      setMessage('Scaffold file approved.');
    }
  };

  const rejectScaffoldFile = async (scaffoldFileId: string) => {
    if (await submit(`/api/v2/pipeline-version-scaffold-files/${scaffoldFileId}/reject`, { comment: scaffoldFileComment })) {
      setScaffoldFileComment('');
      setMessage('Scaffold file rejected. Regenerate it to try again.');
    }
  };

  const canReview = review?.version.review_status === 'pending_review';
  // Being already scheduled is NOT a reason to disable this - the
  // backend's schedule endpoint already updates cron_expression on an
  // existing Schedule row and re-registers it (see register_schedule's
  // replace_existing=True), rather than only ever creating a new one.
  // The only genuine precondition is the version being approved.
  const canSchedule = review?.version.review_status === 'approved';
  const alreadyScheduled = review?.pipeline.status === 'scheduled';
  const isMultiFile = (review?.files?.length ?? 0) > 0;
  const currentFileIndex = review?.files?.findIndex((f) => f.review_status !== 'approved') ?? -1;

  return (
    <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2"><ClipboardCheck className="h-5 w-5 text-violet-400" /> Review &amp; approval gate</h2>
          <p className="mt-1 text-xs text-slate-400">Only a successfully sandbox-tested version can be approved or scheduled.</p>
        </div>
        {review && <span className="rounded-full border border-violet-800 bg-violet-950 px-2.5 py-1 text-xs font-semibold text-violet-300">v{review.version.number} · {review.version.review_status.replace('_', ' ')}</span>}
      </div>

      <form onSubmit={loadReview} className="grid gap-3 md:grid-cols-[1fr_auto]">
        <input value={pipelineId} onChange={(event) => setPipelineId(event.target.value)} placeholder="Pipeline UUID" className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-violet-500 focus:outline-none" />
        <button type="submit" disabled={busy} className="inline-flex items-center justify-center gap-2 rounded-lg border border-violet-700 bg-violet-950 px-4 py-2 text-xs font-semibold text-violet-200 hover:bg-violet-900 disabled:opacity-50"><RefreshCw className={`h-3.5 w-3.5 ${busy ? 'animate-spin' : ''}`} /> Load</button>
      </form>

      {message && <p className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-300">{message}</p>}

      {review && <div className="space-y-4 border-t border-slate-800 pt-5">
        <div className="rounded-lg border border-slate-800 bg-slate-950 p-4">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">Architecture review</p>

          {!review.version.architecture_status && (
            <div className="space-y-2">
              <p className="text-xs text-slate-400">Before any code is generated, the AI assesses whether this goal actually matches the real schema and proposes an approach for you to review.</p>
              <button onClick={proposeArchitecture} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"><Send className="h-3.5 w-3.5" /> Propose architecture</button>
            </div>
          )}

          {review.version.architecture_proposal && (
            <div className="space-y-3">
              <div className={`rounded-lg border p-3 ${review.version.architecture_proposal.feasible ? 'border-emerald-800 bg-emerald-950/40' : 'border-amber-800 bg-amber-950/40'}`}>
                <p className={`text-xs font-semibold ${review.version.architecture_proposal.feasible ? 'text-emerald-400' : 'text-amber-400'}`}>
                  {review.version.architecture_proposal.feasible ? '✓ Feasible' : '⚠ Not feasible as described'}
                </p>
                <p className={`mt-1 text-xs ${review.version.architecture_proposal.feasible ? 'text-emerald-300' : 'text-amber-300'}`}>{review.version.architecture_proposal.feasibility_notes}</p>
              </div>

              <p className="text-xs text-slate-300">{review.version.architecture_proposal.summary}</p>

              {review.version.architecture_proposal.source_tables_used.length > 0 && (
                <div>
                  <p className="text-[11px] font-semibold text-slate-400">Source tables</p>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {review.version.architecture_proposal.source_tables_used.map((t) => (
                      <span key={t} className="rounded-full border border-slate-700 bg-slate-900 px-2 py-0.5 font-mono text-[10px] text-slate-300">{t}</span>
                    ))}
                  </div>
                </div>
              )}

              {review.version.architecture_proposal.destination_table && (
                <p className="text-[11px] text-slate-400">Destination: <span className="font-mono text-slate-300">{review.version.architecture_proposal.destination_dataset}.{review.version.architecture_proposal.destination_table}</span></p>
              )}

              {review.version.architecture_proposal.approach && (
                <p className="text-[11px] text-slate-400">Approach: <span className="text-slate-300">{review.version.architecture_proposal.approach}</span></p>
              )}

              {review.version.architecture_proposal.key_transformations.length > 0 && (
                <div>
                  <p className="text-[11px] font-semibold text-slate-400">Key transformations</p>
                  <ul className="mt-1 space-y-0.5">
                    {review.version.architecture_proposal.key_transformations.map((t, i) => <li key={i} className="text-[11px] text-slate-300">- {t}</li>)}
                  </ul>
                </div>
              )}

              {review.version.architecture_proposal.assumptions.length > 0 && (
                <div>
                  <p className="text-[11px] font-semibold text-slate-400">Assumptions</p>
                  <ul className="mt-1 space-y-0.5">
                    {review.version.architecture_proposal.assumptions.map((a, i) => <li key={i} className="text-[11px] text-slate-300">- {a}</li>)}
                  </ul>
                </div>
              )}

              {review.version.architecture_proposal.files.length > 0 && (
                <div>
                  <p className="text-[11px] font-semibold text-slate-400">Proposed build plan ({review.version.architecture_proposal.files.length} file{review.version.architecture_proposal.files.length === 1 ? '' : 's'})</p>
                  <div className="mt-1.5 space-y-1.5">
                    {review.version.architecture_proposal.files.map((f, i) => (
                      <div key={i} className="rounded-lg border border-slate-800 bg-slate-900 p-2">
                        <p className="font-mono text-[11px] font-semibold text-slate-200">{i + 1}. {f.file_name}</p>
                        <p className="text-[10px] text-slate-400">{f.purpose}</p>
                        <p className="mt-0.5 text-[10px] text-slate-500">Reads: <span className="font-mono">{f.reads_from.join(', ') || '(source only)'}</span> → Writes: <span className="font-mono">{f.destination_table}</span></p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {review.version.architecture_proposal.architecture_diagram_mermaid && (
                <div>
                  <p className="text-[11px] font-semibold text-slate-400">Architecture diagram</p>
                  <div className="mt-1.5">
                    <MermaidDiagram chart={review.version.architecture_proposal.architecture_diagram_mermaid} />
                  </div>
                </div>
              )}

              {(review.version.architecture_proposal.files.length > 0 || review.version.architecture_proposal.project_structure.length > 0) && (
                <div>
                  <p className="text-[11px] font-semibold text-slate-400">Project structure</p>
                  <div className="mt-1.5 rounded-lg border border-slate-800 bg-slate-950 p-3 font-mono text-[11px]">
                    {(() => {
                      const combined = [
                        ...review.version.architecture_proposal.files.map((f) => ({
                          path: f.directory ? `${f.directory}/${f.file_name}` : f.file_name,
                          purpose: f.purpose,
                          generated: true,
                        })),
                        ...review.version.architecture_proposal.project_structure.map((sf) => ({
                          path: sf.path,
                          purpose: sf.purpose,
                          generated: sf.generated,
                        })),
                      ].sort((a, b) => {
                        // Root-level files first, then each subfolder's
                        // contents grouped together, alphabetically
                        // within each group.
                        const aIdx = a.path.lastIndexOf('/');
                        const bIdx = b.path.lastIndexOf('/');
                        const aDir = aIdx === -1 ? '' : a.path.slice(0, aIdx);
                        const bDir = bIdx === -1 ? '' : b.path.slice(0, bIdx);
                        return aDir === bDir ? a.path.localeCompare(b.path) : aDir.localeCompare(bDir);
                      });

                      // Indentation alone doesn't say WHAT a file is
                      // indented under - without an explicit folder row,
                      // indented entries read as nested under whatever
                      // happened to sort immediately above them. Emit a
                      // folder-name row every time the directory changes.
                      const rows: JSX.Element[] = [];
                      let lastDir: string | null = null;
                      combined.forEach((item, i) => {
                        const idx = item.path.lastIndexOf('/');
                        const dir = idx === -1 ? '' : item.path.slice(0, idx);
                        const name = idx === -1 ? item.path : item.path.slice(idx + 1);
                        const dirDepth = dir ? dir.split('/').length : 0;

                        if (dir && dir !== lastDir) {
                          dir.split('/').forEach((segment, segIdx) => {
                            rows.push(
                              <div key={`dir-${i}-${segIdx}`} className="flex items-baseline gap-2 py-0.5" style={{ paddingLeft: `${segIdx * 16}px` }}>
                                <span className="text-slate-600">{segIdx > 0 ? '└─' : '─'}</span>
                                <span className="font-semibold text-cyan-400">{segment}/</span>
                              </div>,
                            );
                          });
                          lastDir = dir;
                        }

                        rows.push(
                          <div key={i} className="flex flex-wrap items-baseline gap-x-2 py-0.5" style={{ paddingLeft: `${dirDepth * 16}px` }}>
                            <span className="text-slate-600">{dirDepth > 0 ? '└─' : '─'}</span>
                            <span className={item.generated ? 'text-slate-200' : 'text-slate-500 italic'}>{name}</span>
                            <span className="text-[10px] text-slate-600">{item.purpose}</span>
                          </div>,
                        );
                      });
                      return rows;
                    })()}
                  </div>
                  <p className="mt-1 text-[10px] text-slate-600">Grey, italicized entries are documentation only — 9Gear Pulse does not generate their content.</p>
                </div>
              )}
            </div>
          )}

          {review.version.architecture_status === 'pending_review' && (
            <div className="mt-3 space-y-2">
              <textarea value={architectureComment} onChange={(e) => setArchitectureComment(e.target.value)} placeholder="Why is this a sound plan, or why is it rejected?" className="h-16 w-full rounded-lg border border-slate-700 bg-slate-900 p-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-violet-500 focus:outline-none" />
              <div className="flex gap-2">
                <button onClick={approveArchitecture} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"><CheckCircle2 className="h-3.5 w-3.5" /> Approve architecture</button>
                <button onClick={rejectArchitecture} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg border border-rose-800 bg-rose-950 px-3 py-2 text-xs font-semibold text-rose-300 hover:bg-rose-900 disabled:opacity-50"><XCircle className="h-3.5 w-3.5" /> Reject architecture</button>
              </div>
            </div>
          )}

          {review.version.architecture_status === 'approved' && (
            <p className="mt-2 text-[11px] text-emerald-400">✓ Architecture approved - ready to generate code.</p>
          )}

          {review.version.architecture_status === 'rejected' && (
            <div className="mt-3">
              <p className="text-[11px] text-rose-400">Architecture rejected.</p>
              <button onClick={proposeArchitecture} disabled={busy} className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"><Send className="h-3.5 w-3.5" /> Propose new architecture</button>
            </div>
          )}
        </div>

        {!isMultiFile && <>
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

        {((review.version.review_status === 'draft' && review.version.architecture_status === 'approved') || review.version.review_status === 'testing') && <button onClick={generateAndTest} disabled={busy || review.version.generation_in_progress} className="inline-flex items-center gap-1.5 rounded-lg bg-cyan-600 px-3 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50"><Send className="h-3.5 w-3.5" /> {review.version.generation_in_progress ? 'Generating...' : review.version.review_status === 'testing' ? 'Retry generate & sandbox test' : 'Generate & sandbox test'}</button>}


        <div className="rounded-lg border border-slate-800 bg-slate-950 p-4">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">Sandbox evidence</p>
          {review.runs.length === 0 ? <p className="text-xs text-amber-400">No sandbox run is recorded for this version.</p> : review.runs.map((run) => (
            <div key={run.id} className="border-t border-slate-800 py-3 first:border-t-0 first:pt-0">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-semibold text-slate-200">{run.status} {run.row_count !== null ? `· ${run.row_count} rows` : ''}</p>
                <button onClick={() => toggleRun(run.id)} className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-400 hover:text-slate-200">
                  {expandedRuns.has(run.id) ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />} {expandedRuns.has(run.id) ? 'Hide log' : 'View log'}
                </button>
              </div>
              {expandedRuns.has(run.id) && (
                <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-xs text-slate-400">{run.log_output || run.error_output || 'No output recorded.'}</pre>
              )}
              {run.quality_checks?.checked && run.quality_checks.warnings && run.quality_checks.warnings.length > 0 && (
                <div className="mt-2 rounded-lg border border-amber-800 bg-amber-950/40 p-2.5">
                  <p className="text-[11px] font-semibold text-amber-400 mb-1">⚠ Data quality warnings — {run.quality_checks.dataset}.{run.quality_checks.table}</p>
                  <ul className="space-y-0.5">
                    {run.quality_checks.warnings.map((w, i) => <li key={i} className="text-[11px] text-amber-300">{w}</li>)}
                  </ul>
                </div>
              )}
              {run.quality_checks?.checked && (!run.quality_checks.warnings || run.quality_checks.warnings.length === 0) && (
                <p className="mt-2 text-[11px] text-emerald-400">✓ No data quality warnings — {run.quality_checks.dataset}.{run.quality_checks.table}</p>
              )}
              {run.quality_checks && !run.quality_checks.checked && (
                <div className="mt-2 rounded-lg border border-amber-800 bg-amber-950/40 p-2.5">
                  <p className="text-[11px] font-semibold text-amber-400">⚠ Data quality check could not run</p>
                  <p className="mt-0.5 text-[11px] text-amber-300">{run.quality_checks.reason}</p>
                </div>
              )}
            </div>
          ))}
        </div>
        </>}

        {isMultiFile && (
          <div className="space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Build plan ({review.files.length} file{review.files.length === 1 ? '' : 's'})</p>
            {review.files.map((file, i) => {
              const isApproved = file.review_status === 'approved';
              const isCurrent = i === currentFileIndex;
              const isLocked = !isApproved && !isCurrent;
              return (
                <div key={file.id} className={`rounded-lg border p-4 ${isApproved ? 'border-emerald-800 bg-emerald-950/20' : isCurrent ? 'border-violet-700 bg-slate-950' : 'border-slate-800 bg-slate-950/50 opacity-60'}`}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="flex h-5 w-5 items-center justify-center rounded-full border border-slate-600 text-[10px] font-bold text-slate-300">{file.file_order}</span>
                      <span className="font-mono text-xs font-semibold text-slate-100">{file.file_name}</span>
                    </div>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${isApproved ? 'bg-emerald-900 text-emerald-300' : 'bg-slate-800 text-slate-400'}`}>{file.review_status.replace('_', ' ')}</span>
                  </div>
                  <p className="mt-1.5 text-[11px] text-slate-400">{file.purpose}</p>
                  <p className="mt-1 text-[11px] text-slate-500">Reads: <span className="font-mono">{file.reads_from.join(', ') || '(source only)'}</span> → Writes: <span className="font-mono text-slate-300">{file.destination_table}</span></p>

                  {isLocked && <p className="mt-2 text-[11px] italic text-slate-600">Waiting for earlier files to be approved.</p>}

                  {isCurrent && review.version.uses_pinned_schema && (
                    <div className="mt-3 rounded-lg border border-indigo-900 bg-indigo-950/20 p-3">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-indigo-300">
                        Destination schema {file.schema_review_status ? `— ${file.schema_review_status.replace('_', ' ')}` : '— not proposed yet'}
                      </p>

                      {!file.schema_review_status || file.schema_review_status === 'rejected' ? (
                        <>
                          {file.schema_review_status === 'rejected' && file.schema_rejection_comment && (
                            <p className="mt-2 text-[11px] text-rose-300">Previous feedback: {file.schema_rejection_comment}</p>
                          )}
                          <button onClick={() => proposeFileSchema(file.id)} disabled={busy}
                            className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50">
                            <ClipboardCheck className="h-3.5 w-3.5" /> {file.schema_review_status === 'rejected' ? 'Propose schema again' : 'Propose schema'}
                          </button>
                        </>
                      ) : (
                        <>
                          {file.schema_ddl && (
                            <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-900 p-3 font-mono text-[11px] text-indigo-200">{file.schema_ddl}</pre>
                          )}
                          {file.schema_review_status === 'approved' ? (
                            <p className="mt-2 text-[11px] text-emerald-400">
                              Applied to the real database{file.schema_applied_at ? ` at ${new Date(file.schema_applied_at).toLocaleString()}` : ''}. This table's structure is now fixed.
                            </p>
                          ) : (
                            <>
                              <p className="mt-2 text-[11px] text-slate-400">Review the DDL above - approving it runs CREATE TABLE against the real database right now, before any code exists for this file.</p>
                              <div className="mt-2 flex flex-wrap items-center gap-2">
                                <button onClick={() => approveFileSchema(file.id)} disabled={busy}
                                  className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50">
                                  <CheckCircle2 className="h-3.5 w-3.5" /> Approve & apply schema
                                </button>
                                <input value={schemaComment} onChange={(e) => setSchemaComment(e.target.value)} placeholder="Rejection reason (optional)"
                                  className="min-w-[12rem] flex-1 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none" />
                                <button onClick={() => rejectFileSchema(file.id)} disabled={busy}
                                  className="inline-flex items-center gap-1.5 rounded-lg border border-rose-800 px-3 py-2 text-xs font-semibold text-rose-300 hover:bg-rose-950 disabled:opacity-50">
                                  <XCircle className="h-3.5 w-3.5" /> Reject
                                </button>
                              </div>
                            </>
                          )}
                        </>
                      )}
                    </div>
                  )}

                  {file.generated_code && (
                    <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-900 p-3 font-mono text-[11px] text-emerald-300">{file.generated_code}</pre>
                  )}

                  {isCurrent && (file.review_status === 'draft' || file.review_status === 'testing' || file.review_status === 'rejected') && (
                    // Same precondition the backend itself enforces: when this
                    // version uses pinned schemas, code generation is disabled
                    // until this file's own schema is approved - matching
                    // generate_and_test_file's own gate exactly, so the button
                    // never leads to a confusing 409 the user has to decode.
                    (!review.version.uses_pinned_schema || file.schema_review_status === 'approved') ? (
                      <button onClick={() => generateFile(file.id)} disabled={busy || file.generation_in_progress} className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-cyan-600 px-3 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50">
                        <Send className="h-3.5 w-3.5" /> {file.generation_in_progress ? 'Generating...' : file.review_status === 'testing' || file.review_status === 'rejected' ? 'Retry generate & sandbox test' : 'Generate & sandbox test'}
                      </button>
                    ) : (
                      <p className="mt-3 text-[11px] italic text-slate-600">Approve this file's schema above before generating its code.</p>
                    )
                  )}

                  {isCurrent && file.runs.length > 0 && (
                    <div className="mt-3 border-t border-slate-800 pt-3">
                      {file.runs.map((run) => (
                        <div key={run.id} className="mb-2">
                          <div className="flex items-center justify-between gap-2">
                            <p className="text-[11px] font-semibold text-slate-300">{run.status} {run.row_count !== null ? `· ${run.row_count} rows` : ''}</p>
                            <button onClick={() => toggleRun(run.id)} className="inline-flex items-center gap-1 text-[10px] font-semibold text-slate-500 hover:text-slate-300">
                              {expandedRuns.has(run.id) ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} {expandedRuns.has(run.id) ? 'Hide log' : 'View log'}
                            </button>
                          </div>
                          {expandedRuns.has(run.id) && (
                            <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-slate-400">{run.log_output || run.error_output || 'No output recorded.'}</pre>
                          )}
                          {run.quality_checks?.checked && run.quality_checks.warnings && run.quality_checks.warnings.length > 0 && (
                            <div className="mt-1.5 rounded-lg border border-amber-800 bg-amber-950/40 p-2">
                              <p className="text-[10px] font-semibold text-amber-400">⚠ Data quality warnings</p>
                              <ul className="space-y-0.5">
                                {run.quality_checks.warnings.map((w, wi) => <li key={wi} className="text-[10px] text-amber-300">{w}</li>)}
                              </ul>
                            </div>
                          )}
                          {run.quality_checks?.checked && (!run.quality_checks.warnings || run.quality_checks.warnings.length === 0) && (
                            <p className="mt-1 text-[10px] text-emerald-400">✓ No data quality warnings</p>
                          )}
                          {run.quality_checks && !run.quality_checks.checked && (
                            <p className="mt-1 text-[10px] text-amber-300">⚠ Data quality check could not run: {run.quality_checks.reason}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  {isCurrent && file.review_status === 'pending_review' && (
                    <div className="mt-3 space-y-2">
                      <textarea value={fileComment} onChange={(e) => setFileComment(e.target.value)} placeholder="Why is this file safe to approve, or why is it rejected?" className="h-16 w-full rounded-lg border border-slate-700 bg-slate-900 p-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-violet-500 focus:outline-none" />
                      <div className="flex gap-2">
                        <button onClick={() => approveFile(file.id)} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"><CheckCircle2 className="h-3.5 w-3.5" /> Approve file</button>
                        <button onClick={() => rejectFile(file.id)} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg border border-rose-800 bg-rose-950 px-3 py-2 text-xs font-semibold text-rose-300 hover:bg-rose-900 disabled:opacity-50"><XCircle className="h-3.5 w-3.5" /> Reject file</button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {review.scaffold_files.length > 0 && (
          <div className="space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Scaffold files ({review.scaffold_files.length})</p>
            {review.scaffold_files.map((sf) => (
              <div key={sf.id} className={`rounded-lg border p-4 ${sf.review_status === 'approved' ? 'border-emerald-800 bg-emerald-950/20' : 'border-slate-800 bg-slate-950'}`}>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-semibold text-slate-100">{sf.path}</span>
                    <span className="rounded-full border border-slate-700 bg-slate-900 px-2 py-0.5 font-mono text-[10px] text-slate-400">{sf.scaffold_type}</span>
                  </div>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${sf.review_status === 'approved' ? 'bg-emerald-900 text-emerald-300' : 'bg-slate-800 text-slate-400'}`}>{sf.review_status.replace('_', ' ')}</span>
                </div>
                <p className="mt-1.5 text-[11px] text-slate-400">{sf.purpose}</p>

                {sf.generated_content && (
                  <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-900 p-3 font-mono text-[11px] text-emerald-300">{sf.generated_content}</pre>
                )}

                {(sf.review_status === 'draft' || sf.review_status === 'testing' || sf.review_status === 'rejected') && (
                  <button onClick={() => generateScaffoldFile(sf.id)} disabled={busy} className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-cyan-600 px-3 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50">
                    <Send className="h-3.5 w-3.5" /> {sf.review_status === 'testing' || sf.review_status === 'rejected' ? 'Retry generate' : 'Generate'}
                  </button>
                )}

                {sf.review_status === 'pending_review' && (
                  <div className="mt-3 space-y-2">
                    <textarea value={scaffoldFileComment} onChange={(e) => setScaffoldFileComment(e.target.value)} placeholder="Why is this file safe to approve, or why is it rejected?" className="h-16 w-full rounded-lg border border-slate-700 bg-slate-900 p-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-violet-500 focus:outline-none" />
                    <div className="flex gap-2">
                      <button onClick={() => approveScaffoldFile(sf.id)} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"><CheckCircle2 className="h-3.5 w-3.5" /> Approve file</button>
                      <button onClick={() => rejectScaffoldFile(sf.id)} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg border border-rose-800 bg-rose-950 px-3 py-2 text-xs font-semibold text-rose-300 hover:bg-rose-900 disabled:opacity-50"><XCircle className="h-3.5 w-3.5" /> Reject file</button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

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

        {review.version.review_status === 'approved' && (
          <button onClick={exportPipeline} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-700 disabled:opacity-50">
            <Download className="h-3.5 w-3.5" /> Export code
          </button>
        )}

        <div className="flex flex-col gap-2 rounded-lg border border-slate-800 bg-slate-950 p-4 sm:flex-row sm:items-end">
          <label className="flex-1 text-xs font-semibold text-slate-300">Approved schedule (five-field cron)<input value={cronExpression} onChange={(event) => setCronExpression(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 focus:border-violet-500 focus:outline-none" /></label>
          <button onClick={schedule} disabled={!canSchedule || busy} className="rounded-lg bg-violet-600 px-3 py-2 text-xs font-semibold text-white hover:bg-violet-500 disabled:cursor-not-allowed disabled:bg-slate-700">{alreadyScheduled ? 'Update schedule' : 'Schedule approved version'}</button>
          {alreadyScheduled && (
            <button onClick={unschedule} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg border border-rose-800 px-3 py-2 text-xs font-semibold text-rose-300 hover:bg-rose-950 disabled:opacity-50">
              <XCircle className="h-3.5 w-3.5" /> Stop schedule
            </button>
          )}
        </div>
      </div>}
    </section>
  );
}
