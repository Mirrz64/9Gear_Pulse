'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useAuth } from '@clerk/nextjs';
import { Database, FolderPlus, Plug } from 'lucide-react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
type Item = { id: string; name: string };

// onPipelineCreated keeps its original (pipelineId, actorId) shape so
// the parent's existing wiring doesn't break before setup/page.tsx gets
// its own matching cleanup pass - the second argument is just an empty
// string now, since there's no actor_id concept in this component
// anymore. onActorReady has nothing left to fire on (there's no more
// "create the actor" step) and is deliberately never called.
export default function ProjectSetup({ onPipelineCreated, onActorReady }: { onPipelineCreated: (pipelineId: string, actorId: string) => void; onActorReady?: (actorId: string) => void }) {
  void onActorReady;
  const { getToken } = useAuth();
  const [profiles, setProfiles] = useState<Item[]>([]); const [projects, setProjects] = useState<Item[]>([]);
  const [name, setName] = useState(''); const [credentials, setCredentials] = useState('{\n  "database_url": "postgresql://user:password@host:5432/database"\n}');
  const [projectName, setProjectName] = useState(''); const [goal, setGoal] = useState('');
  const [projectId, setProjectId] = useState(''); const [sourceId, setSourceId] = useState(''); const [destinationId, setDestinationId] = useState('');
  const [message, setMessage] = useState(''); const [busy, setBusy] = useState(false);

  const request = async (path: string, method: string, body?: object) => {
    const token = await getToken();
    if (!token) throw new Error('You need to be signed in to do that.');
    const response = await fetch(`${API}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data: unknown = await response.json();
    if (!response.ok) throw new Error(typeof data === 'object' && data && 'detail' in data ? String(data.detail) : 'Request failed');
    return data as Record<string, unknown>;
  };

  const refresh = async () => { const [p, c] = await Promise.all([request('/api/v2/projects', 'GET'), request('/api/v2/connection-profiles', 'GET')]); setProjects(p.projects as Item[]); setProfiles(c.connection_profiles as Item[]); };
  const action = async (work: () => Promise<void>) => { setBusy(true); setMessage(''); try { await work(); } catch (e) { setMessage(e instanceof Error ? e.message : 'Request failed'); } finally { setBusy(false); } };

  useEffect(() => {
    void action(refresh);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const createProfile = (e: FormEvent) => { e.preventDefault(); void action(async () => { const parsed = JSON.parse(credentials) as object; await request('/api/v2/connection-profiles', 'POST', { name, type: 'postgres', credentials: parsed }); setName(''); await refresh(); setMessage('Connection profile encrypted and saved.'); }); };
  const createProject = (e: FormEvent) => { e.preventDefault(); void action(async () => { const data = await request('/api/v2/projects', 'POST', { name: projectName, goal_description: goal }); setProjectId(String(data.id)); setProjectName(''); await refresh(); setMessage('Project created. Select it below and create a draft pipeline.'); }); };
  const createPipeline = (e: FormEvent) => { e.preventDefault(); void action(async () => { const data = await request('/api/v2/pipelines', 'POST', { project_id: projectId, source_connection_id: sourceId, destination_connection_id: destinationId, generated_code: '# Draft pipeline. Generate and sandbox-test this version before approval.\n' }); onPipelineCreated(String(data.pipeline_id), ''); setMessage('Draft created and loaded into the review gate. Introspect the source, then generate and test it.'); }); };
  const introspect = () => void action(async () => { if (!sourceId) throw new Error('Select the source connection first.'); await request(`/api/v2/connection-profiles/${sourceId}/introspect`, 'POST'); setMessage('Source schema cached safely (without sample rows).'); });

  return <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-4"><div><h2 className="text-lg font-semibold flex gap-2 items-center"><FolderPlus className="h-5 w-5 text-cyan-400" /> Project setup</h2><p className="text-xs text-slate-400 mt-1">Create your workspace objects in order. Credentials are encrypted before storage.</p></div><div className="grid gap-4 md:grid-cols-3"><form onSubmit={createProfile} className="space-y-2"><p className="text-xs font-semibold text-cyan-300"><Plug className="inline h-3.5" /> Connection profile</p><input required value={name} onChange={e=>setName(e.target.value)} placeholder="Profile name" className="w-full rounded bg-slate-950 border border-slate-700 p-2 text-xs"/><textarea required value={credentials} onChange={e=>setCredentials(e.target.value)} className="w-full h-24 rounded bg-slate-950 border border-slate-700 p-2 font-mono text-xs"/><button disabled={busy} className="rounded bg-slate-800 px-3 py-2 text-xs">Save encrypted profile</button></form><form onSubmit={createProject} className="space-y-2"><p className="text-xs font-semibold text-cyan-300"><FolderPlus className="inline h-3.5" /> Project</p><input required value={projectName} onChange={e=>setProjectName(e.target.value)} placeholder="Project name" className="w-full rounded bg-slate-950 border border-slate-700 p-2 text-xs"/><textarea required value={goal} onChange={e=>setGoal(e.target.value)} placeholder="Pipeline goal" className="w-full h-24 rounded bg-slate-950 border border-slate-700 p-2 text-xs"/><button disabled={busy} className="rounded bg-slate-800 px-3 py-2 text-xs">Create project</button></form><form onSubmit={createPipeline} className="space-y-2"><p className="text-xs font-semibold text-cyan-300"><Database className="inline h-3.5" /> Draft pipeline</p><select required value={projectId} onChange={e=>setProjectId(e.target.value)} className="w-full rounded bg-slate-950 border border-slate-700 p-2 text-xs"><option value="">Select project</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><select required value={sourceId} onChange={e=>setSourceId(e.target.value)} className="w-full rounded bg-slate-950 border border-slate-700 p-2 text-xs"><option value="">Source profile</option>{profiles.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><select required value={destinationId} onChange={e=>setDestinationId(e.target.value)} className="w-full rounded bg-slate-950 border border-slate-700 p-2 text-xs"><option value="">Destination profile</option>{profiles.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><div className="flex gap-2"><button type="button" onClick={introspect} disabled={busy || !sourceId} className="rounded border border-cyan-800 px-3 py-2 text-xs">Introspect</button><button disabled={busy} className="rounded bg-cyan-600 px-3 py-2 text-xs font-semibold">Create draft</button></div></form></div>{message && <p className="text-xs text-slate-300">{message}</p>}</section>;
}
