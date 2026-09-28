// The single source of truth for how connection types are grouped -
// imported by the main /connections tiles page, each category's
// sub-page, and the sidebar's expanded sub-nav. Defined once here so
// all three stay in sync automatically; duplicating this list across
// files would risk them drifting apart the moment a new connector
// type gets added later.

export interface ConnectorCategory {
  slug: string;
  label: string;
  description: string;
  connectors: { type: string; label: string }[];
}

export const CONNECTOR_CATEGORIES: ConnectorCategory[] = [
  {
    slug: 'relational-databases',
    label: 'Relational Databases',
    description: 'Read from or write to a full SQL database.',
    connectors: [
      { type: 'postgres', label: 'Postgres' },
      { type: 'azure_sql', label: 'Azure SQL Database' },
    ],
  },
  {
    slug: 'analytical-warehouses',
    label: 'Analytical Warehouses',
    description: 'Read from or write to a big-data warehouse.',
    connectors: [
      { type: 'snowflake', label: 'Snowflake' },
      { type: 'bigquery', label: 'BigQuery' },
    ],
  },
  {
    slug: 'object-storage',
    label: 'Object Storage',
    description: 'Read files from or write files into a bucket or container.',
    connectors: [
      { type: 's3', label: 'S3 / MinIO' },
      { type: 'azure_blob', label: 'Azure Blob Storage' },
    ],
  },
  {
    slug: 'key-value-store',
    label: 'Key-Value Store',
    description: 'Read from or write to Redis.',
    connectors: [
      { type: 'redis', label: 'Redis' },
    ],
  },
  {
    slug: 'web-api-sources',
    label: 'Web API Sources',
    description: 'Read from a REST API, GraphQL, or SOAP service.',
    connectors: [
      { type: 'api', label: 'REST API' },
      { type: 'graphql', label: 'GraphQL' },
      { type: 'soap', label: 'SOAP' },
    ],
  },
  {
    slug: 'file-uploads',
    label: 'File Uploads',
    description: 'Upload CSV or JSON files directly.',
    connectors: [
      { type: 'file', label: 'File Upload' },
    ],
  },
];

// Single-connector categories route straight to their one connector,
// skipping the intermediate sub-tile page entirely - both the main
// page's own tile links and the sidebar use this so the skip is
// consistent everywhere, not just in one place.
export function categoryHref(category: ConnectorCategory): string {
  if (category.connectors.length === 1) {
    return `/connections/${category.slug}/${category.connectors[0].type}`;
  }
  return `/connections/${category.slug}`;
}
