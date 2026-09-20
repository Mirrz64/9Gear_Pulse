'use client';

import { FormEvent, useEffect, useState } from 'react';
import Link from 'next/link';
import { useAuth } from '@clerk/nextjs';
import { ArrowRight, FolderPlus, Pencil, Trash2, X } from 'lucide-react';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface Project {
  id: string;
  name: string;
  goal_description: string;
  status: string;
  category: string;
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
  // "all" preserves the original, unfiltered behavior by default -
  // filtering is something the user opts into via the tabs below, not
  // a new default that hides projects nobody asked to hide.
  const [categoryFilter, setCategoryFilter] = useState<'all' | 'new' | 'testing' | 'completed'>('all');
  const [message, setMessage] = useState<string | null>(null);
  // Every action here (create, load, update, delete) previously shared
  // one undifferentiated message box - the exact same problem already
  // fixed on connection-profiles.tsx, for the same reason: a real
  // error and a plain success confirmation looked identical.
  const [messageKind, setMessageKind] = useState<'success' | 'error' | null>(null);
  const notify = (kind: 'success' | 'error', text: string) => {
    setMessageKind(kind);
    setMessage(text);
  };
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');

  // Editing an existing project - only one at a time, tracked by id.
  // Unlike connection profiles, a project's fields (name, goal
  // description) are plain, never-encrypted values already visible in
  // the list - so these DO pre-fill with current values, a real edit
  // rather than a blind partial patch.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState('');
  const [editGoal, setEditGoal] = useState('');

  const startEdit = (project: Project) => {
    setEditingId(project.id);
    setEditName(project.name);
    setEditGoal(project.goal_description);
    setMessage(null);
    setMessageKind(null);
  };
  const cancelEdit = () => setEditingId(null);

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
      notify('error', err instanceof Error ? err.message : 'Failed to load projects.');
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
    setMessageKind(null);
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_BASE_URL}/api/v2/projects`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, goal_description: goal }),
      });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      notify('success', `Project "${name}" created.`);
      setName('');
      setGoal('');
      await loadProjects();
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Failed to create project.');
    } finally {
      setCreating(false);
    }
  };

  const saveEdit = async (project: Project) => {
    if (!editName.trim() || !editGoal.trim()) {
      notify('error', 'Name and goal description cannot be empty.');
      return;
    }
    setBusyId(project.id);
    setMessage(null);
    setMessageKind(null);
    try {
      const headers = await authHeaders();
      const body: { name?: string; goal_description?: string } = {};
      if (editName.trim() !== project.name) body.name = editName.trim();
      if (editGoal.trim() !== project.goal_description) body.goal_description = editGoal.trim();
      if (!body.name && !body.goal_description) {
        notify('error', 'Change the name or goal description before saving.');
        return;
      }
      const res = await fetch(`${API_BASE_URL}/api/v2/projects/${project.id}`, {
        method: 'PATCH',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      notify('success', `Updated "${project.name}".`);
      setEditingId(null);
      await loadProjects();
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Update failed.');
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (projectId: string, projectName: string) => {
    if (!window.confirm(`Delete project "${projectName}"? This can't be undone.`)) return;
    setBusyId(projectId);
    setMessage(null);
    setMessageKind(null);
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_BASE_URL}/api/v2/projects/${projectId}`, { method: 'DELETE', headers });
      if (res.status !== 204) {
        const data: unknown = await res.json();
        throw new Error(getErrorMessage(data));
      }
      notify('success', `Deleted "${projectName}".`);
      await loadProjects();
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Delete failed.');
    } finally {
      setBusyId(null);
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

      <section className="flex gap-2">
        {(['all', 'new', 'testing', 'completed'] as const).map((cat) => (
          <button key={cat} onClick={() => setCategoryFilter(cat)}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold capitalize ${categoryFilter === cat ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
            {cat}
          </button>
        ))}
      </section>

      <section className="space-y-2">
        {(() => {
          const filtered = categoryFilter === 'all' ? projects : projects.filter((p) => p.category === categoryFilter);
          return filtered.length === 0 ? (
          <p className="text-xs text-slate-500">{projects.length === 0 ? 'No projects yet - create one above.' : `No ${categoryFilter} projects.`}</p>
        ) : (
          filtered.map((project) => (
            <div key={project.id} className="rounded-lg border border-slate-800 bg-slate-900 p-4 hover:border-cyan-800">
              <div className="flex items-center justify-between gap-3">
                <Link href={`/projects/${project.id}`} className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-slate-100">{project.name}</span>
                    <span className="rounded-full border border-slate-700 bg-slate-950 px-2 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">{project.status}</span>
                    <span className="rounded-full border border-cyan-800 bg-cyan-950/40 px-2 py-0.5 text-[10px] uppercase tracking-wide text-cyan-400">{project.category}</span>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-slate-500">{project.goal_description}</p>
                </Link>
                <div className="flex shrink-0 items-center gap-2">
                  <button onClick={() => (editingId === project.id ? cancelEdit() : startEdit(project))} disabled={busyId === project.id}
                    className="rounded-lg border border-slate-700 p-1.5 text-slate-400 hover:bg-slate-800 disabled:opacity-50"
                    aria-label={editingId === project.id ? `Cancel editing ${project.name}` : `Edit ${project.name}`}>
                    {editingId === project.id ? <X className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}
                  </button>
                  <button onClick={() => remove(project.id, project.name)} disabled={busyId === project.id}
                    className="rounded-lg border border-rose-800 p-1.5 text-rose-400 hover:bg-rose-950 disabled:opacity-50" aria-label={`Delete ${project.name}`}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                  <Link href={`/projects/${project.id}`} aria-label={`Open ${project.name}`}>
                    <ArrowRight className="h-4 w-4 shrink-0 text-slate-600" />
                  </Link>
                </div>
              </div>

              {editingId === project.id && (
                <div className="mt-3 space-y-2 border-t border-slate-800 pt-3">
                  <input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="Project name"
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                  <input value={editGoal} onChange={(e) => setEditGoal(e.target.value)} placeholder="Pipeline goal (plain English)"
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                  <div className="flex gap-2">
                    <button onClick={() => saveEdit(project)} disabled={busyId === project.id}
                      className="rounded-lg bg-cyan-600 px-3 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50">
                      Save changes
                    </button>
                    <button onClick={cancelEdit} disabled={busyId === project.id}
                      className="rounded-lg border border-slate-700 px-3 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-800 disabled:opacity-50">
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))
        );
        })()}
      </section>
    </div>
  );
}
