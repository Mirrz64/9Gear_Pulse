'use client';

import { FormEvent, useEffect, useState } from 'react';
import Link from 'next/link';
import { useAuth } from '@clerk/nextjs';
import { FolderPlus, ArrowRight } from 'lucide-react';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface Project {
  id: string;
  name: string;
  goal_description: string;
  status: string;
  created_at: string;
}

function getErrorMessage(payload: unknown): string {
  if (typeof payload === 'object' && payload && 'detail' in payload) {
    return String((payload as { detail: unknown }).detail);
  }
  return 'The request could not be completed.';
}

export default function ProjectsPage() {
  const { getToken } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');

  const authHeaders = async (): Promise<HeadersInit> => {
    const token = await getToken();
    if (!token) throw new Error('You need to be signed in to do that.');
    return { Authorization: `Bearer ${token}` };
  };

  const loadProjects = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v2/projects`, { headers: await authHeaders() });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      setProjects((data as { projects: Project[] }).projects || []);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to load projects.');
    }
  };

  useEffect(() => {
    void loadProjects();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const createProject = async (event: FormEvent) => {
    event.preventDefault();
    setCreating(true);
    setMessage(null);
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_BASE_URL}/api/v2/projects`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, goal_description: goal }),
      });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      setName('');
      setGoal('');
      setMessage(`Project "${name}" created.`);
      await loadProjects();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to create project.');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-6">
      <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-4">
        <h2 className="text-lg font-semibold flex items-center gap-2"><FolderPlus className="h-5 w-5 text-cyan-400" /> New project</h2>
        <form onSubmit={createProject} className="grid gap-3 md:grid-cols-[1fr_2fr_auto] rounded-lg border border-slate-800 bg-slate-950 p-4">
          <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Project name"
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
          <input required value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Pipeline goal (plain English)"
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
          <button type="submit" disabled={creating}
            className="rounded-lg bg-cyan-600 px-4 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50">
            Create project
          </button>
        </form>
        {message && <p className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-300">{message}</p>}
      </section>

      <section className="space-y-2">
        {projects.length === 0 ? (
          <p className="text-xs text-slate-500">No projects yet - create one above.</p>
        ) : (
          projects.map((project) => (
            <Link
              key={project.id}
              href={`/projects/${project.id}`}
              className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900 p-4 hover:border-cyan-800"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-slate-100">{project.name}</span>
                  <span className="rounded-full border border-slate-700 bg-slate-950 px-2 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">{project.status}</span>
                </div>
                <p className="mt-0.5 truncate text-xs text-slate-500">{project.goal_description}</p>
              </div>
              <ArrowRight className="h-4 w-4 shrink-0 text-slate-600" />
            </Link>
          ))
        )}
      </section>
    </div>
  );
}
