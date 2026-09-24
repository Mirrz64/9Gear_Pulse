import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { CONNECTOR_CATEGORIES } from '../categories';

export default async function ConnectionCategoryPage({
  params,
}: {
  params: Promise<{ category: string }>;
}) {
  const { category: categorySlug } = await params;
  const category = CONNECTOR_CATEGORIES.find((c) => c.slug === categorySlug);
  if (!category) notFound();

  // Safety net for direct navigation (a bookmark, a stale link) - the
  // main page's own tile links and the sidebar both already skip
  // straight to the one connector for a single-connector category, so
  // this redirect should rarely actually fire in practice.
  if (category.connectors.length === 1) {
    redirect(`/connections/${category.slug}/${category.connectors[0].type}`);
  }

  return (
    <div>
      <h1 className="text-2xl font-bold tracking-tight text-slate-100">{category.label}</h1>
      <p className="mt-2 text-sm text-slate-400">{category.description}</p>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {category.connectors.map((connector) => (
          <Link
            key={connector.type}
            href={`/connections/${category.slug}/${connector.type}`}
            className="rounded-xl border border-slate-800 bg-slate-900 p-5 hover:border-cyan-800"
          >
            <h2 className="text-sm font-semibold text-slate-100">{connector.label}</h2>
          </Link>
        ))}
      </div>
    </div>
  );
}
