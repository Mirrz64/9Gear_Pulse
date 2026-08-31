'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useAuth } from '@clerk/nextjs';
import { CalendarClock, ArrowRight } from 'lucide-react';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface ScheduleRow {
  id: string;
  pipeline_id: string;
  cron_expression: string;
  next_run_at: string | null;
  pipeline_version: number;
  project_name: string;
}

function getErrorMessage(payload: unknown): string {
  if (typeof payload === 'object' && payload && 'detail' in payload) {
    return String((payload as { detail: unknown }).detail);
  }
  return 'The request could not be completed.';
}

export default function SchedulesPage() {
  const { getToken } = useAuth();
  const [schedules, setSchedules] = useState<ScheduleRow[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const token = await getToken();
        if (!token) throw new Error('You need to be signed in to do that.');
        const res = await fetch(`${API_BASE_URL}/api/v2/schedules`, { headers: { Authorization: `Bearer ${token}` } });
        const data: unknown = await res.json();
        if (!res.ok) throw new Error(getErrorMessage(data));
        setSchedules((data as { schedules: ScheduleRow[] }).schedules || []);
      } catch (err) {
        setMessage(err instanceof Error ? err.message : 'Failed to load schedules.');
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold flex items-center gap-2 text-slate-100"><CalendarClock className="h-5 w-5 text-emerald-400" /> Schedules</h1>
        <p className="mt-1 text-xs text-slate-400">Recurring runs, pinned to the exact approved version reviewed - a new draft never changes what an active schedule actually runs.</p>
      </div>

      {message && <p className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-300">{message}</p>}

      <section className="space-y-2">
        {schedules.length === 0 ? (
          <p className="text-xs text-slate-500">No active schedules yet - approve a pipeline version and schedule it from its review page.</p>
        ) : (
          schedules.map((schedule) => (
            <Link
              key={schedule.id}
              href={`/pipelines/${schedule.pipeline_id}`}
              className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900 p-4 hover:border-emerald-800"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-slate-100">{schedule.project_name}</span>
                  <span className="rounded-full border border-slate-700 bg-slate-950 px-2 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">v{schedule.pipeline_version}</span>
                </div>
                <p className="mt-0.5 font-mono text-xs text-slate-500">
                  {schedule.cron_expression}
                  {schedule.next_run_at && ` · next run ${new Date(schedule.next_run_at).toLocaleString()}`}
                </p>
              </div>
              <ArrowRight className="h-4 w-4 shrink-0 text-slate-600" />
            </Link>
          ))
        )}
      </section>
    </div>
  );
}
