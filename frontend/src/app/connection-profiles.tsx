'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useAuth } from '@clerk/nextjs';
import { CheckCircle2, Plug, RefreshCw, Trash2 } from 'lucide-react';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface ConnectionProfile {
  id: string;
  name: string;
  type: string;
  schema_metadata_json: Record<string, unknown> | null;
  last_introspected_at: string | null;
}

function getErrorMessage(payload: unknown): string {
  if (typeof payload === 'object' && payload && 'detail' in payload) {
    return String((payload as { detail: unknown }).detail);
  }
  return 'The request could not be completed.';
}

// actorId is no longer used for auth (Bearer tokens replace it, via
// getToken() below) - kept as a prop only so the parent's existing
// wiring doesn't break before setup/page.tsx gets its own matching
// cleanup pass.
export default function ConnectionProfiles({ actorId }: { actorId?: string }) {
  const { getToken } = useAuth();
  const [profiles, setProfiles] = useState<ConnectionProfile[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [name, setName] = useState('');
  const [host, setHost] = useState('');
  const [port, setPort] = useState('5432');
  const [database, setDatabase] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const authHeaders = async (): Promise<HeadersInit> => {
    const token = await getToken();
    if (!token) throw new Error('You need to be signed in to do that.');
    return { Authorization: `Bearer ${token}` };
  };

  const loadProfiles = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles`, { headers: await authHeaders() });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      setProfiles((data as { connection_profiles: ConnectionProfile[] }).connection_profiles || []);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to load connection profiles.');
    }
  };

  useEffect(() => {
    void loadProfiles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const createProfile = async (event: FormEvent) => {
    event.preventDefault();
    setCreating(true);
    setMessage(null);
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          type: 'postgres',
          credentials: { host, port: Number(port) || 5432, database, username, password },
        }),
      });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      setName('');
      setHost('');
      setPort('5432');
      setDatabase('');
      setUsername('');
      setPassword('');
      setMessage(`Connection profile "${name}" saved.`);
      await loadProfiles();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Failed to save connection profile.');
    } finally {
      setCreating(false);
    }
  };

  const introspect = async (profileId: string) => {
    setBusyId(profileId);
    setMessage(null);
    try {
      const headers = await authHeaders();
      // No body needed anymore - introspect no longer takes one now that
      // actor_id isn't part of it.
      const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles/${profileId}/introspect`, {
        method: 'POST',
        headers,
      });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      setMessage('Schema introspected and cached (no sample rows sent to the AI).');
      await loadProfiles();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Introspection failed.');
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (profileId: string, profileName: string) => {
    if (!window.confirm(`Delete connection profile "${profileName}"? This can't be undone.`)) return;
    setBusyId(profileId);
    setMessage(null);
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles/${profileId}`, { method: 'DELETE', headers });
      if (res.status !== 204) {
        const data: unknown = await res.json();
        throw new Error(getErrorMessage(data));
      }
      setMessage(`Deleted "${profileName}".`);
      await loadProfiles();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Delete failed.');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-5">
      <div>
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <Plug className="h-5 w-5 text-cyan-400" /> Connection profiles
        </h2>
        <p className="mt-1 text-xs text-slate-400">
          Only Postgres is supported in v1. Credentials are encrypted before storage and never sent to the AI - only cached schema shape is.
        </p>
      </div>

      <form onSubmit={createProfile} className="grid gap-3 md:grid-cols-3 rounded-lg border border-slate-800 bg-slate-950 p-4">
        <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Profile name (e.g. source)"
          className="md:col-span-3 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
        <input required value={host} onChange={(e) => setHost(e.target.value)} placeholder="Host (e.g. 127.0.0.1)"
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
        <input required value={port} onChange={(e) => setPort(e.target.value)} placeholder="Port" inputMode="numeric"
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
        <input required value={database} onChange={(e) => setDatabase(e.target.value)} placeholder="Database name"
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
        <input required value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Username"
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
        <input required type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Password"
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
        <button type="submit" disabled={creating}
          className="md:col-span-3 inline-flex items-center justify-center gap-1.5 rounded-lg bg-cyan-600 px-3 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50">
          {creating ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Plug className="h-3.5 w-3.5" />}
          Save encrypted profile
        </button>
      </form>

      {message && <p className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-300">{message}</p>}

      <div className="space-y-2">
        {profiles.length === 0 ? (
          <p className="text-xs text-slate-500">No connection profiles yet.</p>
        ) : (
          profiles.map((profile) => (
            <div key={profile.id} className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-slate-100">{profile.name}</span>
                  <span className="rounded-full border border-slate-700 bg-slate-900 px-2 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">{profile.type}</span>
                  {profile.last_introspected_at && (
                    <span className="inline-flex items-center gap-1 text-[10px] text-emerald-400">
                      <CheckCircle2 className="h-3 w-3" /> Introspected
                    </span>
                  )}
                </div>
                <p className="mt-0.5 truncate text-[11px] text-slate-500">
                  {profile.last_introspected_at
                    ? `Last introspected ${new Date(profile.last_introspected_at).toLocaleString()}`
                    : 'Not introspected yet - required before it can be used to generate a pipeline.'}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button onClick={() => introspect(profile.id)} disabled={busyId === profile.id}
                  className="rounded-lg border border-cyan-800 px-2.5 py-1.5 text-[11px] font-semibold text-cyan-300 hover:bg-cyan-950 disabled:opacity-50">
                  {profile.last_introspected_at ? 'Re-introspect' : 'Introspect'}
                </button>
                <button onClick={() => remove(profile.id, profile.name)} disabled={busyId === profile.id}
                  className="rounded-lg border border-rose-800 p-1.5 text-rose-400 hover:bg-rose-950 disabled:opacity-50" aria-label={`Delete ${profile.name}`}>
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
