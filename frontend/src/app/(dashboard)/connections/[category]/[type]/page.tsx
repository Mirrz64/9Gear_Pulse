import { notFound } from 'next/navigation';
import { CONNECTOR_CATEGORIES } from '../../categories';
import ConnectionProfiles, { type SourceType } from '../../../../connection-profiles';

export default async function ConnectorTypePage({
  params,
}: {
  params: Promise<{ category: string; type: string }>;
}) {
  const { category: categorySlug, type } = await params;
  const category = CONNECTOR_CATEGORIES.find((c) => c.slug === categorySlug);
  const connector = category?.connectors.find((c) => c.type === type);
  if (!category || !connector) notFound();

  // Safe here specifically - the lookup above already confirmed type is
  // one of the real connector type strings before this line is ever
  // reached, notFound() having already handled every other case.
  return <ConnectionProfiles filterType={type as SourceType} filterLabel={connector.label} />;
}
