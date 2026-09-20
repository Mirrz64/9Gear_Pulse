'use client';

import { FormEvent, useEffect, useState } from 'react';
import Link from 'next/link';
import { useAuth } from '@clerk/nextjs';
import { Database, ArrowRight, Trash2 } from 'lucide-react';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface Project {
  id: string;
  name: string;
  goal_description: string;
  status: string;
}

interface PipelineSummary {
  id: string;
  status: string;
  version: number;
}

interface ConnectionOption {
  id: string;
  name: string;
}

function getErrorMessage(payload: unknown): string {
  if (typeof payload === 'object' && payload && 'detail' in payload) {
    return String((payload as { detail: unknown }).detail);
  }
  return 'The request could not be completed.';
}

export default function ProjectDetailPage(props: PageProps<'/projects/[id]'>) {
  const { getToken } = useAuth();
  const [projectId, setProjectId] = useState<string | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([]);
  const [profiles, setProfiles] = useState<ConnectionOption[]>([]);
  const [sourceId, setSourceId] = useState('');
  const [destinationId, setDestinationId] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  // Same fix already applied to connections and projects - a real
  // error and a plain confirmation previously looked identical here.
  const [messageKind, setMessageKind] = useState<'success' | 'error' | null>(null);
  const notify = (kind: 'success' | 'error', text: string) => {
    setMessageKind(kind);
    setMessage(text);
  };
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const authHeaders = async (): Promise<HeadersInit> => {
    const token = await getToken();
    if (!token) throw new Error('You need to be signed in to do that.');
    return { Authorization: `Bearer ${token}` };
  };

  const load = async (id: string) => {
    try {
      const headers = await authHeaders();
      const [projectRes, pipelinesRes, profilesRes] = await Promise.all([
        fetch(`${API_BASE_URL}/api/v2/projects/${id}`, { headers }),
        fetch(`${API_BASE_URL}/api/v2/projects/${id}/pipelines`, { headers }),
        fetch(`${API_BASE_URL}/api/v2/connection-profiles`, { headers }),
      ]);
      const projectData: unknown = await projectRes.json();
      const pipelinesData: unknown = await pipelinesRes.json();
      const profilesData: unknown = await profilesRes.json();
      if (!projectRes.ok) throw new Error(getErrorMessage(projectData));
      if (!pipelinesRes.ok) throw new Error(getErrorMessage(pipelinesData));
      if (!profilesRes.ok) throw new Error(getErrorMessage(profilesData));
      setProject(projectData as Project);
      setPipelines((pipelinesData as { pipelines: PipelineSummary[] }).pipelines || []);
      setProfiles((profilesData as { connection_profiles: ConnectionOption[] }).connection_profiles || []);
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Failed to load project.');
    }
  };

  // Page components with dynamic segments receive params as a Promise in
  // this Next.js version - resolved once on mount, then used for every
  // subsequent load() call (e.g. after creating a new pipeline).
  useEffect(() => {
    void props.params.then(({ id }) => {
      setProjectId(id);
      void load(id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const createPipeline = async (event: FormEvent) => {
    event.preventDefault();
    if (!projectId) return;
    setCreating(true);
    setMessage(null);
    setMessageKind(null);
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_BASE_URL}/api/v2/pipelines`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          source_connection_id: sourceId,
          destination_connection_id: destinationId,
          generated_code: '# Draft pipeline. Generate and sandbox-test this version before approval.\n',
        }),
      });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      notify('success', 'Draft pipeline created below - open it to introspect the source, then generate and test it.');
      await load(projectId);
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Failed to create pipeline.');
    } finally {
      setCreating(false);
    }
  };

  const removePipeline = async (pipelineId: string, pipeline: PipelineSummary) => {
    if (!projectId) return;
    // A scheduled pipeline has a LIVE cron job hitting a real
    // destination - the confirmation should say so plainly rather than
    // read identically to deleting an untouched draft.
    const warning = pipeline.status === 'scheduled'
      ? `Delete v${pipeline.version}? It is currently SCHEDULED - this will cancel its active schedule too. This can't be undone.`
      : `Delete v${pipeline.version} (${pipeline.status})? This can't be undone.`;
    if (!window.confirm(warning)) return;
    setBusyId(pipelineId);
    setMessage(null);
    setMessageKind(null);
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_BASE_URL}/api/v2/pipelines/${pipelineId}`, { method: 'DELETE', headers });
      if (res.status !== 204) {
        const data: unknown = await res.json();
        throw new Error(getErrorMessage(data));
      }
      notify('success', `Deleted v${pipeline.version}.`);
      await load(projectId);
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Delete failed.');
    } finally {
      setBusyId(null);
    }
  };

  if (!project) {
    return <p className="text-xs text-slate-500">{message || 'Loading project...'}</p>;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-slate-100">{project.name}</h1>
        <p className="mt-1 text-xs text-slate-400">{project.goal_description}</p>
      </div>

      <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-4">
        <h2 className="text-sm font-semibold flex items-center gap-2"><Database className="h-4 w-4 text-cyan-400" /> New draft pipeline</h2>
        <form onSubmit={createPipeline} className="grid gap-3 md:grid-cols-[1fr_1fr_auto] rounded-lg border border-slate-800 bg-slate-950 p-4">
          <select required value={sourceId} onChange={(e) => setSourceId(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 focus:border-cyan-500 focus:outline-none">
            <option value="">Source profile</option>
            {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <select required value={destinationId} onChange={(e) => setDestinationId(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 focus:border-cyan-500 focus:outline-none">
            <option value="">Destination profile</option>
            {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button type="submit" disabled={creating}
            className="rounded-lg bg-cyan-600 px-4 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50">
            Create draft
          </button>
        </form>
        {message && (
          <p className={`rounded-lg border px-3 py-2 text-xs ${
            messageKind === 'error'
              ? 'border-rose-800 bg-rose-950/40 text-rose-300'
              : messageKind === 'success'
                ? 'border-emerald-800 bg-emerald-950/40 text-emerald-300'
                : 'border-slate-700 bg-slate-950 text-slate-300'
          }`}>
            {message}
          </p>
        )}
      </section>

      <section className="space-y-2">
        {pipelines.length === 0 ? (
          <p className="text-xs text-slate-500">No pipelines in this project yet - create one above.</p>
        ) : (
          pipelines.map((pipeline) => (
            <div key={pipeline.id} className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900 p-4 hover:border-violet-800">
              <Link href={`/pipelines/${pipeline.id}`} className="flex flex-1 items-center gap-2">
                <span className="text-sm font-semibold text-slate-100">v{pipeline.version}</span>
                <span className="rounded-full border border-slate-700 bg-slate-950 px-2 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">{pipeline.status}</span>
              </Link>
              <div className="flex shrink-0 items-center gap-2">
                <button onClick={() => removePipeline(pipeline.id, pipeline)} disabled={busyId === pipeline.id}
                  className="rounded-lg border border-rose-800 p-1.5 text-rose-400 hover:bg-rose-950 disabled:opacity-50" aria-label={`Delete v${pipeline.version}`}>
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
                <Link href={`/pipelines/${pipeline.id}`} aria-label={`Open v${pipeline.version}`}>
                  <ArrowRight className="h-4 w-4 shrink-0 text-slate-600" />
                </Link>
              </div>
            </div>
          ))
        )}
      </section>
    </div>
  );
}
