import Link from 'next/link';
import { CONNECTOR_CATEGORIES, categoryHref } from './categories';

export default function ConnectionsPage() {
  return (
    <div>
      <h1 className="text-2xl font-bold tracking-tight text-slate-100">Connections</h1>
      <p className="mt-2 text-sm text-slate-400">Pick a category to create or manage connection profiles.</p>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {CONNECTOR_CATEGORIES.map((category) => (
          <Link
            key={category.slug}
            href={categoryHref(category)}
            className="rounded-xl border border-slate-800 bg-slate-900 p-5 hover:border-cyan-800"
          >
            <h2 className="text-sm font-semibold text-slate-100">{category.label}</h2>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">{category.description}</p>
            <p className="mt-3 text-[11px] text-slate-500">
              {category.connectors.map((c) => c.label).join(' \u00b7 ')}
            </p>
          </Link>
        ))}
      </div>
    </div>
  );
}
