'use client';

import Image from 'next/image';
import Link from 'next/link';
import { SignInButton, SignUpButton } from '@clerk/nextjs';
import { Bot, ShieldCheck, ClipboardCheck, RefreshCw, Lock, ArrowRight } from 'lucide-react';

const features = [
  {
    icon: Bot,
    color: 'text-indigo-400',
    bg: 'bg-indigo-950 border-indigo-800',
    title: 'AI Generation',
    body: 'Describe a pipeline goal in plain English. Claude Sonnet 5 writes it, with GPT-4o as an automatic fallback - structured, schema-enforced output, not a guess.',
  },
  {
    icon: RefreshCw,
    color: 'text-amber-400',
    bg: 'bg-amber-950 border-amber-800',
    title: 'Self-Healing Sandbox',
    body: 'Every pipeline runs in an isolated container against your real destination before anyone sees it. Real failures get diagnosed and repaired automatically, up to three attempts.',
  },
  {
    icon: ClipboardCheck,
    color: 'text-violet-400',
    bg: 'bg-violet-950 border-violet-800',
    title: 'Human Review Gate',
    body: "Nothing reaches production on an AI's say-so. Every version needs an explicit approval, with the full code diff and sandbox evidence in front of you.",
  },
  {
    icon: ShieldCheck,
    color: 'text-emerald-400',
    bg: 'bg-emerald-950 border-emerald-800',
    title: 'Data Quality Checks',
    body: "Catches what a green checkmark can hide - a column that loaded but came back silently empty, or a table that was never created at all.",
  },
  {
    icon: Lock,
    color: 'text-cyan-400',
    bg: 'bg-cyan-950 border-cyan-800',
    title: 'Secure by Design',
    body: 'Credentials are encrypted at rest and never sent to the AI - only cached schema shape is. Every action is tied to a verified, authenticated identity.',
  },
];

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -top-32 left-1/4 h-96 w-96 rounded-full bg-cyan-500/10 blur-3xl" />
        <div className="absolute top-1/3 right-1/4 h-96 w-96 rounded-full bg-violet-500/10 blur-3xl" />
      </div>

      <header className="relative border-b border-slate-800">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-8 py-5">
          <div className="flex items-center gap-2">
            <Image src="/logo-icon.png" alt="9Gear Pulse" width={28} height={28} className="rounded" />
            <span className="text-sm font-bold tracking-tight">9Gear Pulse</span>
          </div>
          <div className="flex items-center gap-3 text-xs font-semibold">
            <SignInButton forceRedirectUrl="/projects">
              <button className="rounded-lg px-3 py-2 text-slate-300 hover:text-white">Sign in</button>
            </SignInButton>
            <SignUpButton forceRedirectUrl="/projects">
              <button className="rounded-lg bg-cyan-600 px-3 py-2 text-white hover:bg-cyan-500">Sign up</button>
            </SignUpButton>
          </div>
        </div>
      </header>

      <section className="relative mx-auto max-w-3xl px-8 pb-20 pt-20 text-center">
        <Image src="/logo-icon.png" alt="" width={72} height={72} className="mx-auto rounded-lg" />
        <p className="mt-3 font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">Data in motion. Insights in real time.</p>
        <h1 className="mt-4 text-4xl font-extrabold tracking-tight sm:text-5xl">
          From plain-English intent to <span className="bg-gradient-to-r from-cyan-400 to-violet-400 bg-clip-text text-transparent">reviewed, scheduled pipelines</span>
        </h1>
        <p className="mx-auto mt-6 max-w-xl text-sm leading-relaxed text-slate-400">
          Describe a data pipeline goal. AI writes it, tests it in a sandbox, and self-heals real bugs -
          but nothing ever touches your real data until a human reviews and approves it.
        </p>

        <div className="mx-auto mt-8 max-w-md rounded-xl border border-slate-800 bg-slate-900/60 px-6 py-4">
          <p className="font-mono text-sm italic text-violet-300">&ldquo;Describe the pipeline. Review what it builds. Ship it.&rdquo;</p>
        </div>

        <div className="mt-10 flex items-center justify-center gap-3">
          <SignUpButton forceRedirectUrl="/projects">
            <button className="inline-flex items-center gap-1.5 rounded-lg bg-cyan-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-cyan-500">
              Get started <ArrowRight className="h-4 w-4" />
            </button>
          </SignUpButton>
          <Link href="/sign-in" className="rounded-lg border border-slate-700 px-5 py-2.5 text-sm font-semibold text-slate-300 hover:bg-slate-900">
            Sign in
          </Link>
        </div>
      </section>

      <section className="relative mx-auto max-w-5xl px-8 pb-24">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {features.map((f) => (
            <div key={f.title} className={`rounded-xl border bg-slate-900 p-5 ${f.bg.split(' ')[1]}`}>
              <div className={`mb-3 inline-flex h-9 w-9 items-center justify-center rounded-lg border ${f.bg}`}>
                <f.icon className={`h-4.5 w-4.5 ${f.color}`} />
              </div>
              <h3 className="text-sm font-semibold text-slate-100">{f.title}</h3>
              <p className="mt-1.5 text-xs leading-relaxed text-slate-400">{f.body}</p>
            </div>
          ))}
        </div>
      </section>

      <footer className="relative border-t border-slate-800 py-6 text-center text-[11px] text-slate-500">
        9Gear Pulse - AI-Reviewed ETL Pipeline Platform
      </footer>
    </main>
  );
}
