import Link from 'next/link';
import { Bot, ShieldCheck, ClipboardCheck, RefreshCw, FolderKanban, Plug, CalendarClock, ArrowRight } from 'lucide-react';

const shortcuts = [
  { href: '/projects', icon: FolderKanban, title: 'Projects', body: 'Start a new build, or pick up where you left off.' },
  { href: '/connections', icon: Plug, title: 'Connections', body: 'Manage the sources and destinations your pipelines read and write.' },
  { href: '/schedules', icon: CalendarClock, title: 'Schedules', body: "See what's running automatically, and when." },
];

const howItWorks = [
  {
    icon: Bot,
    color: 'text-indigo-400',
    bg: 'bg-indigo-950 border-indigo-800',
    title: 'Describe the goal',
    body: 'Say what you want in plain English. Claude Sonnet 5 writes the pipeline, with GPT-4o as an automatic fallback.',
  },
  {
    icon: RefreshCw,
    color: 'text-amber-400',
    bg: 'bg-amber-950 border-amber-800',
    title: 'Sandbox & self-heal',
    body: 'Every version runs in an isolated container against your real destination first. Real failures get diagnosed and repaired automatically.',
  },
  {
    icon: ClipboardCheck,
    color: 'text-violet-400',
    bg: 'bg-violet-950 border-violet-800',
    title: 'You review and approve',
    body: "Nothing reaches production on an AI's say-so - every version needs your explicit approval, with the full diff and sandbox evidence in front of you.",
  },
  {
    icon: ShieldCheck,
    color: 'text-emerald-400',
    bg: 'bg-emerald-950 border-emerald-800',
    title: 'Schedule with confidence',
    body: 'Data quality checks catch what a green checkmark can hide, before a pipeline ever runs unattended.',
  },
];

export default function HomePage() {
  return (
    <div>
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">Welcome to</p>
      <h1 className="mt-2 text-2xl font-extrabold tracking-tight sm:text-3xl">9Gear Pulse</h1>
      <p className="mt-3 max-w-xl text-sm leading-relaxed text-slate-400">
        An AI-reviewed ETL pipeline platform: describe a goal in plain English, and get a pipeline that&apos;s
        been generated, sandbox-tested, self-healed, and reviewed by you before it ever touches real data.
      </p>

      <div className="mt-10 grid gap-3 sm:grid-cols-3">
        {shortcuts.map((s) => (
          <Link key={s.href} href={s.href} className="group rounded-xl border border-slate-800 bg-slate-900 p-5 hover:border-cyan-800">
            <s.icon className="h-5 w-5 text-cyan-400" />
            <h3 className="mt-3 flex items-center gap-1 text-sm font-semibold text-slate-100">
              {s.title} <ArrowRight className="h-3.5 w-3.5 text-slate-600 transition-transform group-hover:translate-x-0.5" />
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">{s.body}</p>
          </Link>
        ))}
      </div>

      <h2 className="mt-14 text-xs font-semibold uppercase tracking-wide text-slate-500">How it works</h2>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {howItWorks.map((f) => (
          <div key={f.title} className={`rounded-xl border bg-slate-900 p-5 ${f.bg.split(' ')[1]}`}>
            <div className={`mb-3 inline-flex h-9 w-9 items-center justify-center rounded-lg border ${f.bg}`}>
              <f.icon className={`h-4.5 w-4.5 ${f.color}`} />
            </div>
            <h3 className="text-sm font-semibold text-slate-100">{f.title}</h3>
            <p className="mt-1.5 text-xs leading-relaxed text-slate-400">{f.body}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
