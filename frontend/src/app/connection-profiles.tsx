'use client';

import { ChangeEvent, FormEvent, useEffect, useState } from 'react';
import { useAuth } from '@clerk/nextjs';
import { CheckCircle2, Pencil, Plug, RefreshCw, Trash2, Upload, X } from 'lucide-react';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

export type SourceType = 'postgres' | 'azure_sql' | 'snowflake' | 's3' | 'azure_blob' | 'bigquery' | 'api' | 'file' | 'redis' | 'graphql' | 'soap';

// These four were deliberately scoped destination-only this session -
// reading from them as a source would need new introspection logic
// (Snowflake/BigQuery: a backend-side driver never confirmed installed;
// S3/Azure Blob: listing bucket objects and sampling files, a genuinely
// different mechanism from SQL reflection) that was never built. The
// backend's introspect endpoint has no branch for any of them and
// always rejects - not a bug, but the UI needs to know this so it
// doesn't show an "Introspect" button that can only ever fail.
const DESTINATION_ONLY_TYPES = new Set(['bigquery']);

interface ConnectionProfile {
  id: string;
  name: string;
  type: string;
  schema_metadata_json: Record<string, unknown> | null;
  last_introspected_at: string | null;
}

interface ProfileFile {
  id: string;
  original_filename: string;
  format: string;
  size_bytes: number | null;
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
export default function ConnectionProfiles({
  actorId,
  filterType,
  filterLabel,
}: {
  actorId?: string;
  // When provided, locks the create form to this one type (hiding the
  // full type selector) and filters the list to just this type -
  // backing the new per-connector pages. Omitted entirely, the
  // component behaves exactly as it always has: every type selectable,
  // every profile listed. filterLabel is the connector's own display
  // name (e.g. "Postgres"), passed separately rather than derived
  // in here from categories.ts - a generic, reusable component
  // shouldn't depend on that route-specific data file.
  filterType?: SourceType;
  filterLabel?: string;
}) {
  const { getToken } = useAuth();
  const [profiles, setProfiles] = useState<ConnectionProfile[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  // Every action in this component (load, introspect, delete, save,
  // now update) shared ONE undifferentiated message box - a real error
  // like a 409 conflict and a plain success confirmation rendered
  // identically, which is exactly what made a correctly-surfaced
  // delete failure read as "nothing happened." notify() below tags
  // which is which so the box can actually look different.
  const [messageKind, setMessageKind] = useState<'success' | 'error' | null>(null);
  const notify = (kind: 'success' | 'error', text: string) => {
    setMessageKind(kind);
    setMessage(text);
  };
  const [busyId, setBusyId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // Editing an existing profile - only ONE at a time, tracked by id.
  // Fields start blank (never pre-filled): the backend never returns
  // decrypted credentials, even to the owner, so there's nothing to
  // show as a "current" value - the update endpoint is a partial patch
  // by design, so leaving a field blank and only filling in what's
  // actually changing is the correct, intended workflow here, not a
  // limitation to work around.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [managingFilesId, setManagingFilesId] = useState<string | null>(null);
  const [profileFiles, setProfileFiles] = useState<ProfileFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesMessage, setFilesMessage] = useState<string | null>(null);
  const [editName, setEditName] = useState('');
  const [editHost, setEditHost] = useState('');
  const [editPort, setEditPort] = useState('');
  const [editDatabase, setEditDatabase] = useState('');
  const [editUsername, setEditUsername] = useState('');
  const [editPassword, setEditPassword] = useState('');
  const [editSchema, setEditSchema] = useState('');
  const [editAzureSqlHost, setEditAzureSqlHost] = useState('');
  const [editAzureSqlPort, setEditAzureSqlPort] = useState('');
  const [editAzureSqlDatabase, setEditAzureSqlDatabase] = useState('');
  const [editAzureSqlUsername, setEditAzureSqlUsername] = useState('');
  const [editAzureSqlPassword, setEditAzureSqlPassword] = useState('');
  const [editAzureSqlSchema, setEditAzureSqlSchema] = useState('');
  const [editSnowflakeAccount, setEditSnowflakeAccount] = useState('');
  const [editSnowflakeUsername, setEditSnowflakeUsername] = useState('');
  const [editSnowflakePassword, setEditSnowflakePassword] = useState('');
  const [editSnowflakeDatabase, setEditSnowflakeDatabase] = useState('');
  const [editSnowflakeSchema, setEditSnowflakeSchema] = useState('');
  const [editSnowflakeWarehouse, setEditSnowflakeWarehouse] = useState('');
  const [editSnowflakeRole, setEditSnowflakeRole] = useState('');
  const [editS3Bucket, setEditS3Bucket] = useState('');
  const [editS3AccessKeyId, setEditS3AccessKeyId] = useState('');
  const [editS3SecretAccessKey, setEditS3SecretAccessKey] = useState('');
  const [editS3Region, setEditS3Region] = useState('');
  const [editS3EndpointUrl, setEditS3EndpointUrl] = useState('');
  const [editS3Prefix, setEditS3Prefix] = useState('');
  const [editAzureBlobContainer, setEditAzureBlobContainer] = useState('');
  const [editAzureBlobAccountName, setEditAzureBlobAccountName] = useState('');
  const [editAzureBlobAccountKey, setEditAzureBlobAccountKey] = useState('');
  const [editAzureBlobPrefix, setEditAzureBlobPrefix] = useState('');
  const [editBigqueryServiceAccountJson, setEditBigqueryServiceAccountJson] = useState('');
  const [editBigqueryLocation, setEditBigqueryLocation] = useState('');
  const [editBaseUrl, setEditBaseUrl] = useState('');
  const [editAuthHeaderRows, setEditAuthHeaderRows] = useState<{ key: string; value: string }[]>([{ key: '', value: '' }]);
  // null means "leave the current method unchanged" - a toggle has no
  // natural blank state the way a text input does, so this is the
  // equivalent of every other edit field starting empty.
  const [editApiMethod, setEditApiMethod] = useState<'GET' | 'POST' | null>(null);
  const [editApiRequestBody, setEditApiRequestBody] = useState('');
  // Changing pagination here REPLACES the entire existing config, same
  // "replace the whole set" semantics as auth headers below - the
  // backend's PATCH does a shallow merge, so a "pagination" key present
  // in the patch at all replaces the whole nested object, never merges
  // into it field by field.
  const [editPaginationChanged, setEditPaginationChanged] = useState(false);
  const [editPaginationStyle, setEditPaginationStyle] = useState<'offset' | 'page' | 'cursor'>('offset');
  const [editPaginationOffsetParam, setEditPaginationOffsetParam] = useState('offset');
  const [editPaginationLimitParam, setEditPaginationLimitParam] = useState('limit');
  const [editPaginationPageParam, setEditPaginationPageParam] = useState('page');
  const [editPaginationPageSizeParam, setEditPaginationPageSizeParam] = useState('');
  const [editPaginationPageSize, setEditPaginationPageSize] = useState('100');
  const [editPaginationCursorParam, setEditPaginationCursorParam] = useState('cursor');
  const [editPaginationNextCursorField, setEditPaginationNextCursorField] = useState('');
  // null = don't touch auth at all (the default, "unchanged" state,
  // same convention as editApiMethod above). 'headers' shows the
  // existing auth-header-rows UI; 'oauth2' shows the OAuth2 fields -
  // only one is ever visible/fillable at a time, so there's no way to
  // accidentally build a patch containing both, which the backend
  // would reject as ambiguous.
  const [editApiAuthMethod, setEditApiAuthMethod] = useState<'headers' | 'oauth2' | null>(null);
  const [editOauth2TokenUrl, setEditOauth2TokenUrl] = useState('');
  const [editOauth2ClientId, setEditOauth2ClientId] = useState('');
  const [editOauth2ClientSecret, setEditOauth2ClientSecret] = useState('');
  const [editOauth2Scope, setEditOauth2Scope] = useState('');
  const [editGraphqlEndpoint, setEditGraphqlEndpoint] = useState('');
  const [editGraphqlQuery, setEditGraphqlQuery] = useState('');
  const [editGraphqlVariables, setEditGraphqlVariables] = useState('');
  const [editSoapEndpoint, setEditSoapEndpoint] = useState('');
  const [editSoapVersion, setEditSoapVersion] = useState<'1.1' | '1.2' | null>(null);
  const [editSoapAction, setEditSoapAction] = useState('');
  const [editSoapRequestBody, setEditSoapRequestBody] = useState('');
  const [editRedisHost, setEditRedisHost] = useState('');
  const [editRedisPort, setEditRedisPort] = useState('');
  const [editRedisUsername, setEditRedisUsername] = useState('');
  const [editRedisPassword, setEditRedisPassword] = useState('');
  const [editRedisDb, setEditRedisDb] = useState('');

  const startEdit = (profile: ConnectionProfile) => {
    setEditingId(profile.id);
    setEditName(profile.name);
    setEditHost(''); setEditPort(''); setEditDatabase(''); setEditUsername(''); setEditPassword(''); setEditSchema('');
    setEditAzureSqlHost(''); setEditAzureSqlPort(''); setEditAzureSqlDatabase(''); setEditAzureSqlUsername(''); setEditAzureSqlPassword(''); setEditAzureSqlSchema('');
    setEditSnowflakeAccount(''); setEditSnowflakeUsername(''); setEditSnowflakePassword(''); setEditSnowflakeDatabase(''); setEditSnowflakeSchema(''); setEditSnowflakeWarehouse(''); setEditSnowflakeRole('');
    setEditS3Bucket(''); setEditS3AccessKeyId(''); setEditS3SecretAccessKey(''); setEditS3Region(''); setEditS3EndpointUrl(''); setEditS3Prefix('');
    setEditAzureBlobContainer(''); setEditAzureBlobAccountName(''); setEditAzureBlobAccountKey(''); setEditAzureBlobPrefix('');
    setEditBigqueryServiceAccountJson(''); setEditBigqueryLocation('');
    setEditBaseUrl(''); setEditAuthHeaderRows([{ key: '', value: '' }]);
    setEditApiMethod(null); setEditApiRequestBody('');
    setEditPaginationChanged(false); setEditPaginationStyle('offset');
    setEditPaginationOffsetParam('offset'); setEditPaginationLimitParam('limit');
    setEditPaginationPageParam('page'); setEditPaginationPageSizeParam(''); setEditPaginationPageSize('100');
    setEditPaginationCursorParam('cursor'); setEditPaginationNextCursorField('');
    setEditApiAuthMethod(null);
    setEditOauth2TokenUrl(''); setEditOauth2ClientId(''); setEditOauth2ClientSecret(''); setEditOauth2Scope('');
    setEditGraphqlEndpoint(''); setEditGraphqlQuery(''); setEditGraphqlVariables('');
    setEditSoapEndpoint(''); setEditSoapVersion(null); setEditSoapAction(''); setEditSoapRequestBody('');
    setEditRedisHost(''); setEditRedisPort(''); setEditRedisUsername(''); setEditRedisPassword(''); setEditRedisDb('');
    setMessage(null);
    setMessageKind(null);
  };
  const cancelEdit = () => setEditingId(null);

  const addEditAuthHeaderRow = () => setEditAuthHeaderRows((rows) => [...rows, { key: '', value: '' }]);
  const removeEditAuthHeaderRow = (index: number) =>
    setEditAuthHeaderRows((rows) => (rows.length === 1 ? rows : rows.filter((_, i) => i !== index)));
  const updateEditAuthHeaderRow = (index: number, field: 'key' | 'value', value: string) =>
    setEditAuthHeaderRows((rows) => rows.map((row, i) => (i === index ? { ...row, [field]: value } : row)));

  const [sourceType, setSourceType] = useState<SourceType>(filterType ?? 'postgres');
  const [name, setName] = useState('');

  // Postgres fields
  const [host, setHost] = useState('');
  const [port, setPort] = useState('5432');
  const [database, setDatabase] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [schema, setSchema] = useState('');
  // Azure SQL gets its own state rather than reusing Postgres's fields
  // above, matching the established convention for every other
  // connection type this session (redisHost vs host, soapEndpoint vs
  // graphqlEndpoint) - avoids any cross-contamination if a user
  // toggles between types while filling in the form.
  const [azureSqlHost, setAzureSqlHost] = useState('');
  const [azureSqlPort, setAzureSqlPort] = useState('1433');
  const [azureSqlDatabase, setAzureSqlDatabase] = useState('');
  const [azureSqlUsername, setAzureSqlUsername] = useState('');
  const [azureSqlPassword, setAzureSqlPassword] = useState('');
  const [azureSqlSchema, setAzureSqlSchema] = useState('');
  const [snowflakeAccount, setSnowflakeAccount] = useState('');
  const [snowflakeUsername, setSnowflakeUsername] = useState('');
  const [snowflakePassword, setSnowflakePassword] = useState('');
  const [snowflakeDatabase, setSnowflakeDatabase] = useState('');
  const [snowflakeSchema, setSnowflakeSchema] = useState('');
  const [snowflakeWarehouse, setSnowflakeWarehouse] = useState('');
  const [snowflakeRole, setSnowflakeRole] = useState('');
  const [s3Bucket, setS3Bucket] = useState('');
  const [s3AccessKeyId, setS3AccessKeyId] = useState('');
  const [s3SecretAccessKey, setS3SecretAccessKey] = useState('');
  const [s3Region, setS3Region] = useState('');
  const [s3EndpointUrl, setS3EndpointUrl] = useState('');
  const [s3Prefix, setS3Prefix] = useState('');
  const [azureBlobContainer, setAzureBlobContainer] = useState('');
  const [azureBlobAccountName, setAzureBlobAccountName] = useState('');
  const [azureBlobAccountKey, setAzureBlobAccountKey] = useState('');
  const [azureBlobPrefix, setAzureBlobPrefix] = useState('');
  const [bigqueryServiceAccountJson, setBigqueryServiceAccountJson] = useState('');
  const [bigqueryLocation, setBigqueryLocation] = useState('');

  // API fields - auth is a repeatable list of header name/value rows
  // rather than a single api-key field, since some APIs (e.g. RapidAPI)
  // require more than one custom header at once. Empty rows are
  // filtered out on submit, so a no-auth public API just leaves every
  // row blank.
  const [baseUrl, setBaseUrl] = useState('');
  const [authHeaderRows, setAuthHeaderRows] = useState<{ key: string; value: string }[]>([{ key: '', value: '' }]);
  const [apiMethod, setApiMethod] = useState<'GET' | 'POST'>('GET');
  const [apiRequestBody, setApiRequestBody] = useState('');
  const [paginationEnabled, setPaginationEnabled] = useState(false);
  const [paginationStyle, setPaginationStyle] = useState<'offset' | 'page' | 'cursor'>('offset');
  const [paginationOffsetParam, setPaginationOffsetParam] = useState('offset');
  const [paginationLimitParam, setPaginationLimitParam] = useState('limit');
  const [paginationPageParam, setPaginationPageParam] = useState('page');
  const [paginationPageSizeParam, setPaginationPageSizeParam] = useState('');
  const [paginationPageSize, setPaginationPageSize] = useState('100');
  const [paginationCursorParam, setPaginationCursorParam] = useState('cursor');
  const [paginationNextCursorField, setPaginationNextCursorField] = useState('');
  // Mutually exclusive with auth header rows - selecting oauth2 here
  // means auth_headers is omitted from credentials entirely, matching
  // the backend's own mutual-exclusivity validation.
  const [apiAuthMethod, setApiAuthMethod] = useState<'headers' | 'oauth2'>('headers');
  const [oauth2TokenUrl, setOauth2TokenUrl] = useState('');
  const [oauth2ClientId, setOauth2ClientId] = useState('');
  const [oauth2ClientSecret, setOauth2ClientSecret] = useState('');
  const [oauth2Scope, setOauth2Scope] = useState('');
  // GraphQL reuses apiAuthMethod/authHeaderRows/oauth2* directly above -
  // only endpoint, query, and variables are genuinely new fields.
  const [graphqlEndpoint, setGraphqlEndpoint] = useState('');
  const [graphqlQuery, setGraphqlQuery] = useState('');
  const [graphqlVariables, setGraphqlVariables] = useState('');
  // SOAP reuses apiAuthMethod/authHeaderRows/oauth2* directly above,
  // same as GraphQL does - only these four fields are genuinely new.
  const [soapEndpoint, setSoapEndpoint] = useState('');
  const [soapVersion, setSoapVersion] = useState<'1.1' | '1.2'>('1.1');
  const [soapAction, setSoapAction] = useState('');
  const [soapRequestBody, setSoapRequestBody] = useState('');

  const addAuthHeaderRow = () => setAuthHeaderRows((rows) => [...rows, { key: '', value: '' }]);
  const removeAuthHeaderRow = (index: number) =>
    setAuthHeaderRows((rows) => (rows.length === 1 ? rows : rows.filter((_, i) => i !== index)));
  const updateAuthHeaderRow = (index: number, field: 'key' | 'value', value: string) =>
    setAuthHeaderRows((rows) => rows.map((row, i) => (i === index ? { ...row, [field]: value } : row)));

  // File fields - staged locally until submit, same add/remove
  // pattern as auth header rows. Selecting again ADDS to the staged
  // list rather than replacing it, so files picked from separate
  // folders in separate dialogs still end up in one profile together.
  const [stagedFiles, setStagedFiles] = useState<File[]>([]);

  const onFilesSelected = (event: ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(event.target.files || []);
    setStagedFiles((files) => [...files, ...picked]);
    event.target.value = ''; // lets the same filename be re-picked after being removed
  };
  const removeStagedFile = (index: number) =>
    setStagedFiles((files) => files.filter((_, i) => i !== index));

  // Redis fields - db is Redis's numbered logical database (0-15,
  // default 0), a genuinely different concept from Postgres's named
  // database. No redis_url escape hatch field here, matching Postgres's
  // own create form - it doesn't expose its database_url shortcut in
  // the UI either, just the structured fields.
  const [redisHost, setRedisHost] = useState('');
  const [redisPort, setRedisPort] = useState('6379');
  const [redisUsername, setRedisUsername] = useState('');
  const [redisPassword, setRedisPassword] = useState('');
  const [redisDb, setRedisDb] = useState('0');
  const [redisTls, setRedisTls] = useState(false);

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
      notify('error', err instanceof Error ? err.message : 'Failed to load connection profiles.');
    }
  };

  useEffect(() => {
    void loadProfiles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const resetForm = () => {
    setName('');
    setHost('');
    setPort('5432');
    setDatabase('');
    setUsername('');
    setPassword('');
    setSchema('');
    setAzureSqlHost('');
    setAzureSqlPort('1433');
    setAzureSqlDatabase('');
    setAzureSqlUsername('');
    setAzureSqlPassword('');
    setAzureSqlSchema('');
    setSnowflakeAccount('');
    setSnowflakeUsername('');
    setSnowflakePassword('');
    setSnowflakeDatabase('');
    setSnowflakeSchema('');
    setSnowflakeWarehouse('');
    setSnowflakeRole('');
    setS3Bucket('');
    setS3AccessKeyId('');
    setS3SecretAccessKey('');
    setS3Region('');
    setS3EndpointUrl('');
    setS3Prefix('');
    setAzureBlobContainer('');
    setAzureBlobAccountName('');
    setAzureBlobAccountKey('');
    setAzureBlobPrefix('');
    setBigqueryServiceAccountJson('');
    setBigqueryLocation('');
    setBaseUrl('');
    setAuthHeaderRows([{ key: '', value: '' }]);
    setApiMethod('GET');
    setApiRequestBody('');
    setPaginationEnabled(false);
    setPaginationStyle('offset');
    setPaginationOffsetParam('offset');
    setPaginationLimitParam('limit');
    setPaginationPageParam('page');
    setPaginationPageSizeParam('');
    setPaginationPageSize('100');
    setPaginationCursorParam('cursor');
    setPaginationNextCursorField('');
    setApiAuthMethod('headers');
    setOauth2TokenUrl('');
    setOauth2ClientId('');
    setOauth2ClientSecret('');
    setOauth2Scope('');
    setGraphqlEndpoint('');
    setGraphqlQuery('');
    setGraphqlVariables('');
    setSoapEndpoint('');
    setSoapVersion('1.1');
    setSoapAction('');
    setSoapRequestBody('');
    setStagedFiles([]);
    setRedisHost('');
    setRedisPort('6379');
    setRedisUsername('');
    setRedisPassword('');
    setRedisDb('0');
    setRedisTls(false);
  };

  const createProfile = async (event: FormEvent) => {
    event.preventDefault();
    setCreating(true);
    setMessage(null);
    setMessageKind(null);
    try {
      const headers = await authHeaders();

      if (sourceType === 'file') {
        if (stagedFiles.length === 0) {
          throw new Error('Add at least one CSV or JSON file.');
        }
        const formData = new FormData();
        formData.append('name', name);
        stagedFiles.forEach((file) => formData.append('files', file));
        // No Content-Type header here - the browser sets the correct
        // multipart boundary itself; setting it manually breaks the
        // request the same way it would for any FormData upload.
        const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles/file`, {
          method: 'POST',
          headers,
          body: formData,
        });
        const data: unknown = await res.json();
        if (!res.ok) throw new Error(getErrorMessage(data));
        notify('success', `Connection profile "${name}" saved with ${stagedFiles.length} file(s).`);
        resetForm();
        await loadProfiles();
        return;
      }

      const authHeaderPairs = authHeaderRows.reduce<Record<string, string>>((acc, row) => {
        if (row.key.trim() && row.value.trim()) acc[row.key.trim()] = row.value;
        return acc;
      }, {});
      let parsedRequestBody: Record<string, unknown> = {};
      if (sourceType === 'api' && apiMethod === 'POST' && apiRequestBody.trim()) {
        try {
          parsedRequestBody = JSON.parse(apiRequestBody);
        } catch {
          throw new Error('Request body must be valid JSON.');
        }
      }
      let parsedGraphqlVariables: Record<string, unknown> = {};
      if (sourceType === 'graphql' && graphqlVariables.trim()) {
        try {
          parsedGraphqlVariables = JSON.parse(graphqlVariables);
        } catch {
          throw new Error('Variables must be valid JSON.');
        }
      }
      const pagination = paginationEnabled
        ? paginationStyle === 'offset'
          ? { style: 'offset', offset_param: paginationOffsetParam, limit_param: paginationLimitParam, page_size: Number(paginationPageSize) || 100 }
          : paginationStyle === 'page'
          ? { style: 'page', page_param: paginationPageParam, ...(paginationPageSizeParam.trim() ? { page_size_param: paginationPageSizeParam.trim() } : {}), page_size: Number(paginationPageSize) || 100 }
          : { style: 'cursor', cursor_param: paginationCursorParam, next_cursor_field: paginationNextCursorField }
        : null;
      const authFields =
        apiAuthMethod === 'headers'
          ? (Object.keys(authHeaderPairs).length ? { auth_headers: authHeaderPairs } : {})
          : apiAuthMethod === 'oauth2'
          ? { oauth2: { token_url: oauth2TokenUrl, client_id: oauth2ClientId, client_secret: oauth2ClientSecret, ...(oauth2Scope.trim() ? { scope: oauth2Scope.trim() } : {}) } }
          : {};
      const credentials =
        sourceType === 'postgres'
          ? { host, port: Number(port) || 5432, database, username, password, ...(schema.trim() ? { schema: schema.trim() } : {}) }
          : sourceType === 'azure_sql'
          ? { host: azureSqlHost, port: Number(azureSqlPort) || 1433, database: azureSqlDatabase, username: azureSqlUsername, password: azureSqlPassword, ...(azureSqlSchema.trim() ? { schema: azureSqlSchema.trim() } : {}) }
          : sourceType === 'snowflake'
          ? {
              account: snowflakeAccount,
              username: snowflakeUsername,
              password: snowflakePassword,
              database: snowflakeDatabase,
              ...(snowflakeSchema.trim() ? { schema: snowflakeSchema.trim() } : {}),
              ...(snowflakeWarehouse.trim() ? { warehouse: snowflakeWarehouse.trim() } : {}),
              ...(snowflakeRole.trim() ? { role: snowflakeRole.trim() } : {}),
            }
          : sourceType === 's3'
          ? {
              bucket: s3Bucket,
              aws_access_key_id: s3AccessKeyId,
              aws_secret_access_key: s3SecretAccessKey,
              ...(s3Region.trim() ? { region: s3Region.trim() } : {}),
              ...(s3EndpointUrl.trim() ? { endpoint_url: s3EndpointUrl.trim() } : {}),
              ...(s3Prefix.trim() ? { prefix: s3Prefix.trim() } : {}),
            }
          : sourceType === 'azure_blob'
          ? {
              container: azureBlobContainer,
              azure_storage_account_name: azureBlobAccountName,
              azure_storage_account_key: azureBlobAccountKey,
              ...(azureBlobPrefix.trim() ? { prefix: azureBlobPrefix.trim() } : {}),
            }
          : sourceType === 'bigquery'
          ? {
              service_account_json: bigqueryServiceAccountJson,
              ...(bigqueryLocation.trim() ? { location: bigqueryLocation.trim() } : {}),
            }
          : sourceType === 'redis'
          ? {
              host: redisHost,
              port: Number(redisPort) || 6379,
              db: Number(redisDb) || 0,
              ...(redisUsername.trim() ? { username: redisUsername.trim() } : {}),
              ...(redisPassword ? { password: redisPassword } : {}),
              ...(redisTls ? { tls: true } : {}),
            }
          : sourceType === 'graphql'
          ? {
              endpoint: graphqlEndpoint,
              query: graphqlQuery,
              ...(Object.keys(parsedGraphqlVariables).length ? { variables: parsedGraphqlVariables } : {}),
              ...authFields,
            }
          : sourceType === 'soap'
          ? {
              endpoint: soapEndpoint,
              soap_version: soapVersion,
              ...(soapAction.trim() ? { soap_action: soapAction.trim() } : {}),
              request_body: soapRequestBody,
              ...authFields,
            }
          : {
              base_url: baseUrl,
              method: apiMethod,
              ...authFields,
              ...(apiMethod === 'POST' && Object.keys(parsedRequestBody).length ? { request_body: parsedRequestBody } : {}),
              ...(pagination ? { pagination } : {}),
            };
      const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, type: sourceType, credentials }),
      });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      notify('success', `Connection profile "${name}" saved.`);
      resetForm();
      await loadProfiles();
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Failed to save connection profile.');
    } finally {
      setCreating(false);
    }
  };

  const introspect = async (profileId: string) => {
    setBusyId(profileId);
    setMessage(null);
    setMessageKind(null);
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
      notify('success', 'Schema introspected and cached (no sample rows sent to the AI).');
      await loadProfiles();
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Introspection failed.');
    } finally {
      setBusyId(null);
    }
  };

  const openFileManager = async (profileId: string) => {
    if (managingFilesId === profileId) {
      setManagingFilesId(null);
      return;
    }
    setManagingFilesId(profileId);
    setProfileFiles([]);
    setFilesMessage(null);
    setFilesLoading(true);
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles/${profileId}/files`, { headers });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      setProfileFiles((data as { files: ProfileFile[] }).files);
    } catch (err) {
      setFilesMessage(err instanceof Error ? err.message : 'Could not load files.');
    } finally {
      setFilesLoading(false);
    }
  };

  const refreshProfileFiles = async (profileId: string) => {
    const headers = await authHeaders();
    const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles/${profileId}/files`, { headers });
    const data: unknown = await res.json();
    if (!res.ok) throw new Error(getErrorMessage(data));
    setProfileFiles((data as { files: ProfileFile[] }).files);
  };

  const addFiles = async (profileId: string, fileList: FileList) => {
    if (fileList.length === 0) return;
    setFilesLoading(true);
    setFilesMessage(null);
    try {
      const headers = await authHeaders();
      const formData = new FormData();
      Array.from(fileList).forEach((f) => formData.append('files', f));
      // Deliberately no Content-Type header here - the browser sets
      // the correct multipart/form-data boundary itself when the body
      // is a FormData object; setting it manually would omit that
      // boundary and break the upload.
      const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles/${profileId}/files`, {
        method: 'POST', headers, body: formData,
      });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      setFilesMessage('File(s) added.');
      await refreshProfileFiles(profileId);
      await loadProfiles();
    } catch (err) {
      setFilesMessage(err instanceof Error ? err.message : 'Could not add file(s).');
    } finally {
      setFilesLoading(false);
    }
  };

  const replaceFile = async (profileId: string, fileId: string, file: File) => {
    setFilesLoading(true);
    setFilesMessage(null);
    try {
      const headers = await authHeaders();
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles/${profileId}/files/${fileId}`, {
        method: 'PUT', headers, body: formData,
      });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      setFilesMessage('File replaced.');
      await refreshProfileFiles(profileId);
      await loadProfiles();
    } catch (err) {
      setFilesMessage(err instanceof Error ? err.message : 'Could not replace file.');
    } finally {
      setFilesLoading(false);
    }
  };

  const removeFile = async (profileId: string, fileId: string, filename: string) => {
    if (!window.confirm(`Remove "${filename}" from this profile?`)) return;
    setFilesLoading(true);
    setFilesMessage(null);
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles/${profileId}/files/${fileId}`, {
        method: 'DELETE', headers,
      });
      if (!res.ok) {
        const data: unknown = await res.json();
        throw new Error(getErrorMessage(data));
      }
      setFilesMessage('File removed.');
      await refreshProfileFiles(profileId);
      await loadProfiles();
    } catch (err) {
      setFilesMessage(err instanceof Error ? err.message : 'Could not remove file.');
    } finally {
      setFilesLoading(false);
    }
  };

  const remove = async (profileId: string, profileName: string) => {
    if (!window.confirm(`Delete connection profile "${profileName}"? This can't be undone.`)) return;
    setBusyId(profileId);
    setMessage(null);
    setMessageKind(null);
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles/${profileId}`, { method: 'DELETE', headers });
      if (res.status !== 204) {
        const data: unknown = await res.json();
        throw new Error(getErrorMessage(data));
      }
      notify('success', `Deleted "${profileName}".`);
      await loadProfiles();
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Delete failed.');
    } finally {
      setBusyId(null);
    }
  };

  const saveEdit = async (profile: ConnectionProfile) => {
    setBusyId(profile.id);
    setMessage(null);
    setMessageKind(null);
    try {
      const headers = await authHeaders();
      const body: { name?: string; credentials?: Record<string, unknown> } = {};

      if (editName.trim() && editName.trim() !== profile.name) body.name = editName.trim();

      if (profile.type === 'postgres') {
        const creds: Record<string, unknown> = {};
        if (editHost.trim()) creds.host = editHost.trim();
        if (editPort.trim()) creds.port = Number(editPort) || undefined;
        if (editDatabase.trim()) creds.database = editDatabase.trim();
        if (editUsername.trim()) creds.username = editUsername.trim();
        if (editPassword) creds.password = editPassword;
        if (editSchema.trim()) creds.schema = editSchema.trim();
        if (Object.keys(creds).length) body.credentials = creds;
      } else if (profile.type === 'azure_sql') {
        const creds: Record<string, unknown> = {};
        if (editAzureSqlHost.trim()) creds.host = editAzureSqlHost.trim();
        if (editAzureSqlPort.trim()) creds.port = Number(editAzureSqlPort) || undefined;
        if (editAzureSqlDatabase.trim()) creds.database = editAzureSqlDatabase.trim();
        if (editAzureSqlUsername.trim()) creds.username = editAzureSqlUsername.trim();
        if (editAzureSqlPassword) creds.password = editAzureSqlPassword;
        if (editAzureSqlSchema.trim()) creds.schema = editAzureSqlSchema.trim();
        if (Object.keys(creds).length) body.credentials = creds;
      } else if (profile.type === 'snowflake') {
        const creds: Record<string, unknown> = {};
        if (editSnowflakeAccount.trim()) creds.account = editSnowflakeAccount.trim();
        if (editSnowflakeUsername.trim()) creds.username = editSnowflakeUsername.trim();
        if (editSnowflakePassword) creds.password = editSnowflakePassword;
        if (editSnowflakeDatabase.trim()) creds.database = editSnowflakeDatabase.trim();
        if (editSnowflakeSchema.trim()) creds.schema = editSnowflakeSchema.trim();
        if (editSnowflakeWarehouse.trim()) creds.warehouse = editSnowflakeWarehouse.trim();
        if (editSnowflakeRole.trim()) creds.role = editSnowflakeRole.trim();
        if (Object.keys(creds).length) body.credentials = creds;
      } else if (profile.type === 's3') {
        const creds: Record<string, unknown> = {};
        if (editS3Bucket.trim()) creds.bucket = editS3Bucket.trim();
        if (editS3AccessKeyId.trim()) creds.aws_access_key_id = editS3AccessKeyId.trim();
        if (editS3SecretAccessKey) creds.aws_secret_access_key = editS3SecretAccessKey;
        if (editS3Region.trim()) creds.region = editS3Region.trim();
        if (editS3EndpointUrl.trim()) creds.endpoint_url = editS3EndpointUrl.trim();
        if (editS3Prefix.trim()) creds.prefix = editS3Prefix.trim();
        if (Object.keys(creds).length) body.credentials = creds;
      } else if (profile.type === 'azure_blob') {
        const creds: Record<string, unknown> = {};
        if (editAzureBlobContainer.trim()) creds.container = editAzureBlobContainer.trim();
        if (editAzureBlobAccountName.trim()) creds.azure_storage_account_name = editAzureBlobAccountName.trim();
        if (editAzureBlobAccountKey) creds.azure_storage_account_key = editAzureBlobAccountKey;
        if (editAzureBlobPrefix.trim()) creds.prefix = editAzureBlobPrefix.trim();
        if (Object.keys(creds).length) body.credentials = creds;
      } else if (profile.type === 'bigquery') {
        const creds: Record<string, unknown> = {};
        if (editBigqueryServiceAccountJson.trim()) creds.service_account_json = editBigqueryServiceAccountJson.trim();
        if (editBigqueryLocation.trim()) creds.location = editBigqueryLocation.trim();
        if (Object.keys(creds).length) body.credentials = creds;
      } else if (profile.type === 'redis') {
        const creds: Record<string, unknown> = {};
        if (editRedisHost.trim()) creds.host = editRedisHost.trim();
        if (editRedisPort.trim()) creds.port = Number(editRedisPort) || undefined;
        if (editRedisDb.trim()) creds.db = Number(editRedisDb) || undefined;
        if (editRedisUsername.trim()) creds.username = editRedisUsername.trim();
        if (editRedisPassword) creds.password = editRedisPassword;
        if (Object.keys(creds).length) body.credentials = creds;
      } else if (profile.type === 'api' || profile.type === 'graphql' || profile.type === 'soap') {
        const creds: Record<string, unknown> = {};
        const buildAuthCreds = (): Record<string, unknown> => {
          if (editApiAuthMethod === 'headers') {
            const headerPairs = editAuthHeaderRows.reduce<Record<string, string>>((acc, row) => {
              if (row.key.trim() && row.value.trim()) acc[row.key.trim()] = row.value;
              return acc;
            }, {});
            return Object.keys(headerPairs).length ? { auth_headers: headerPairs } : {};
          }
          if (editApiAuthMethod === 'oauth2') {
            if (!editOauth2TokenUrl.trim() || !editOauth2ClientId.trim() || !editOauth2ClientSecret.trim()) {
              throw new Error('OAuth2 requires token URL, client ID, and client secret - fill in all three, or switch back to unchanged.');
            }
            return {
              oauth2: {
                token_url: editOauth2TokenUrl.trim(),
                client_id: editOauth2ClientId.trim(),
                client_secret: editOauth2ClientSecret,
                ...(editOauth2Scope.trim() ? { scope: editOauth2Scope.trim() } : {}),
              },
            };
          }
          return {};
        };
        if (profile.type === 'api') {
          if (editBaseUrl.trim()) creds.base_url = editBaseUrl.trim();
          if (editApiMethod) creds.method = editApiMethod;
          Object.assign(creds, buildAuthCreds());
          if (editApiMethod === 'POST' && editApiRequestBody.trim()) {
            try {
              creds.request_body = JSON.parse(editApiRequestBody);
            } catch {
              throw new Error('Request body must be valid JSON.');
            }
          }
          if (editPaginationChanged) {
            creds.pagination =
              editPaginationStyle === 'offset'
                ? { style: 'offset', offset_param: editPaginationOffsetParam, limit_param: editPaginationLimitParam, page_size: Number(editPaginationPageSize) || 100 }
                : editPaginationStyle === 'page'
                ? { style: 'page', page_param: editPaginationPageParam, ...(editPaginationPageSizeParam.trim() ? { page_size_param: editPaginationPageSizeParam.trim() } : {}), page_size: Number(editPaginationPageSize) || 100 }
                : { style: 'cursor', cursor_param: editPaginationCursorParam, next_cursor_field: editPaginationNextCursorField };
          }
        } else if (profile.type === 'graphql') {
          if (editGraphqlEndpoint.trim()) creds.endpoint = editGraphqlEndpoint.trim();
          if (editGraphqlQuery.trim()) creds.query = editGraphqlQuery.trim();
          Object.assign(creds, buildAuthCreds());
          if (editGraphqlVariables.trim()) {
            try {
              creds.variables = JSON.parse(editGraphqlVariables);
            } catch {
              throw new Error('Variables must be valid JSON.');
            }
          }
        } else {
          if (editSoapEndpoint.trim()) creds.endpoint = editSoapEndpoint.trim();
          if (editSoapVersion) creds.soap_version = editSoapVersion;
          if (editSoapAction.trim()) creds.soap_action = editSoapAction.trim();
          if (editSoapRequestBody.trim()) creds.request_body = editSoapRequestBody.trim();
          Object.assign(creds, buildAuthCreds());
        }
        if (Object.keys(creds).length) body.credentials = creds;
      }

      if (!body.name && !body.credentials) {
        throw new Error('Change at least one field before saving.');
      }

      const res = await fetch(`${API_BASE_URL}/api/v2/connection-profiles/${profile.id}`, {
        method: 'PATCH',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data: unknown = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data));
      notify('success', `Updated "${profile.name}". Re-introspect to refresh its cached schema.`);
      setEditingId(null);
      await loadProfiles();
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Update failed.');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-100 flex items-center gap-2">
          <Plug className="h-6 w-6 text-cyan-400" /> {filterLabel ? `${filterLabel} connections` : 'Connection profiles'}
        </h1>
        <p className="mt-1 text-xs text-slate-400">
          {filterType
            ? `Manage your ${filterLabel} connection profiles. Credentials are encrypted before storage and never sent to the AI - only cached schema shape is.`
            : 'Postgres, REST API, GraphQL, SOAP, Redis, and file upload (CSV/JSON) sources are supported. Credentials are encrypted before storage and never sent to the AI - only cached schema shape is.'}
        </p>
      </div>

      <form onSubmit={createProfile} className="space-y-3 rounded-lg border border-slate-800 bg-slate-950 p-4">
        {!filterType && (
        <div className="flex gap-2">
          <button type="button" onClick={() => setSourceType('postgres')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${sourceType === 'postgres' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
            Postgres
          </button>
          <button type="button" onClick={() => setSourceType('azure_sql')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${sourceType === 'azure_sql' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
            Azure SQL
          </button>
          <button type="button" onClick={() => setSourceType('snowflake')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${sourceType === 'snowflake' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
            Snowflake
          </button>
          <button type="button" onClick={() => setSourceType('s3')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${sourceType === 's3' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
            S3 / MinIO
          </button>
          <button type="button" onClick={() => setSourceType('azure_blob')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${sourceType === 'azure_blob' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
            Azure Blob Storage
          </button>
          <button type="button" onClick={() => setSourceType('bigquery')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${sourceType === 'bigquery' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
            BigQuery
          </button>
          <button type="button" onClick={() => setSourceType('api')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${sourceType === 'api' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
            REST API
          </button>
          <button type="button" onClick={() => setSourceType('file')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${sourceType === 'file' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
            File Upload
          </button>
          <button type="button" onClick={() => setSourceType('redis')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${sourceType === 'redis' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
            Redis
          </button>
          <button type="button" onClick={() => setSourceType('graphql')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${sourceType === 'graphql' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
            GraphQL
          </button>
          <button type="button" onClick={() => setSourceType('soap')}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${sourceType === 'soap' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
            SOAP
          </button>
        </div>
        )}

        <div className="grid gap-3 md:grid-cols-3">
          <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Profile name (e.g. source)"
            className="md:col-span-3 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />

          {sourceType === 'postgres' ? (
            <>
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
              <input value={schema} onChange={(e) => setSchema(e.target.value)} placeholder="Schema (default: public)"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
            </>
          ) : sourceType === 'azure_sql' ? (
            <>
              <input required value={azureSqlHost} onChange={(e) => setAzureSqlHost(e.target.value)} placeholder="Server (e.g. myserver.database.windows.net)"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input required value={azureSqlPort} onChange={(e) => setAzureSqlPort(e.target.value)} placeholder="Port" inputMode="numeric"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input required value={azureSqlDatabase} onChange={(e) => setAzureSqlDatabase(e.target.value)} placeholder="Database name"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input required value={azureSqlUsername} onChange={(e) => setAzureSqlUsername(e.target.value)} placeholder="Username"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input required type="password" value={azureSqlPassword} onChange={(e) => setAzureSqlPassword(e.target.value)} placeholder="Password"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input value={azureSqlSchema} onChange={(e) => setAzureSqlSchema(e.target.value)} placeholder="Schema (default: dbo)"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
            </>
          ) : sourceType === 'snowflake' ? (
            <>
              <input required value={snowflakeAccount} onChange={(e) => setSnowflakeAccount(e.target.value)} placeholder="Account identifier (e.g. kgiotue-wn98412)"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input required value={snowflakeDatabase} onChange={(e) => setSnowflakeDatabase(e.target.value)} placeholder="Database name"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input required value={snowflakeUsername} onChange={(e) => setSnowflakeUsername(e.target.value)} placeholder="Username"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input required type="password" value={snowflakePassword} onChange={(e) => setSnowflakePassword(e.target.value)} placeholder="Password"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input value={snowflakeSchema} onChange={(e) => setSnowflakeSchema(e.target.value)} placeholder="Schema (default: PUBLIC)"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input value={snowflakeWarehouse} onChange={(e) => setSnowflakeWarehouse(e.target.value)} placeholder="Warehouse (optional)"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input value={snowflakeRole} onChange={(e) => setSnowflakeRole(e.target.value)} placeholder="Role (optional)"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
            </>
          ) : sourceType === 's3' ? (
            <>
              <input required value={s3Bucket} onChange={(e) => setS3Bucket(e.target.value)} placeholder="Bucket name"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input required value={s3AccessKeyId} onChange={(e) => setS3AccessKeyId(e.target.value)} placeholder="Access key ID"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input required type="password" value={s3SecretAccessKey} onChange={(e) => setS3SecretAccessKey(e.target.value)} placeholder="Secret access key"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input value={s3Region} onChange={(e) => setS3Region(e.target.value)} placeholder="Region (optional)"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input value={s3EndpointUrl} onChange={(e) => setS3EndpointUrl(e.target.value)} placeholder="Endpoint URL (for MinIO or other S3-compatible services)"
                className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input value={s3Prefix} onChange={(e) => setS3Prefix(e.target.value)} placeholder="Prefix (optional - only read objects under this folder, e.g. exports/2026/)"
                className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
            </>
          ) : sourceType === 'azure_blob' ? (
            <>
              <input required value={azureBlobContainer} onChange={(e) => setAzureBlobContainer(e.target.value)} placeholder="Container name"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input required value={azureBlobAccountName} onChange={(e) => setAzureBlobAccountName(e.target.value)} placeholder="Storage account name"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input required type="password" value={azureBlobAccountKey} onChange={(e) => setAzureBlobAccountKey(e.target.value)} placeholder="Storage account key"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input value={azureBlobPrefix} onChange={(e) => setAzureBlobPrefix(e.target.value)} placeholder="Prefix (optional - only read blobs under this folder, e.g. exports/2026/)"
                className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
            </>
          ) : sourceType === 'bigquery' ? (
            <>
              <div className="md:col-span-3 space-y-1">
                <p className="text-[11px] text-slate-400">Service account key (paste the whole downloaded JSON file)</p>
                <textarea required value={bigqueryServiceAccountJson} onChange={(e) => setBigqueryServiceAccountJson(e.target.value)} placeholder='{"type": "service_account", "project_id": "...", "private_key": "...", "client_email": "...", ...}'
                  className="h-32 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              </div>
              <input value={bigqueryLocation} onChange={(e) => setBigqueryLocation(e.target.value)} placeholder="Location (default: US)"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
            </>
          ) : sourceType === 'api' ? (
            <>
              <input required value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="Base URL (e.g. https://api.example.com/v1/users)"
                className="md:col-span-3 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <div className="md:col-span-3 flex gap-2">
                <button type="button" onClick={() => setApiMethod('GET')}
                  className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${apiMethod === 'GET' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                  GET
                </button>
                <button type="button" onClick={() => setApiMethod('POST')}
                  className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${apiMethod === 'POST' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                  POST
                </button>
              </div>
              {apiMethod === 'POST' && (
                <div className="md:col-span-3 space-y-1">
                  <p className="text-[11px] text-slate-400">Request body (JSON) - sent with every call this profile makes, e.g. a search/reporting endpoint's query payload</p>
                  <textarea value={apiRequestBody} onChange={(e) => setApiRequestBody(e.target.value)} placeholder='{"query": "...", "page_size": 100}'
                    className="h-20 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                </div>
              )}
              <div className="md:col-span-3 space-y-2 rounded-lg border border-slate-800 bg-slate-950 p-3">
                <label className="flex items-center gap-2 text-xs text-slate-300">
                  <input type="checkbox" checked={paginationEnabled} onChange={(e) => setPaginationEnabled(e.target.checked)} className="accent-cyan-500" />
                  This API is paginated (walk multiple pages, not just one response)
                </label>
                {paginationEnabled && (
                  <div className="space-y-2 pt-1">
                    <div className="flex gap-2">
                      <button type="button" onClick={() => setPaginationStyle('offset')}
                        className={`rounded-lg px-3 py-1.5 text-[11px] font-semibold ${paginationStyle === 'offset' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                        Offset/limit
                      </button>
                      <button type="button" onClick={() => setPaginationStyle('page')}
                        className={`rounded-lg px-3 py-1.5 text-[11px] font-semibold ${paginationStyle === 'page' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                        Page number
                      </button>
                      <button type="button" onClick={() => setPaginationStyle('cursor')}
                        className={`rounded-lg px-3 py-1.5 text-[11px] font-semibold ${paginationStyle === 'cursor' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                        Cursor
                      </button>
                    </div>
                    {paginationStyle === 'offset' && (
                      <div className="grid gap-2 md:grid-cols-3">
                        <input value={paginationOffsetParam} onChange={(e) => setPaginationOffsetParam(e.target.value)} placeholder="Offset param name (e.g. offset)"
                          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                        <input value={paginationLimitParam} onChange={(e) => setPaginationLimitParam(e.target.value)} placeholder="Limit param name (e.g. limit)"
                          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                        <input value={paginationPageSize} onChange={(e) => setPaginationPageSize(e.target.value)} placeholder="Records per page" inputMode="numeric"
                          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      </div>
                    )}
                    {paginationStyle === 'page' && (
                      <div className="grid gap-2 md:grid-cols-3">
                        <input value={paginationPageParam} onChange={(e) => setPaginationPageParam(e.target.value)} placeholder="Page param name (e.g. page)"
                          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                        <input value={paginationPageSizeParam} onChange={(e) => setPaginationPageSizeParam(e.target.value)} placeholder="Page size param (optional)"
                          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                        <input value={paginationPageSize} onChange={(e) => setPaginationPageSize(e.target.value)} placeholder="Records per page" inputMode="numeric"
                          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      </div>
                    )}
                    {paginationStyle === 'cursor' && (
                      <div className="grid gap-2 md:grid-cols-2">
                        <input value={paginationCursorParam} onChange={(e) => setPaginationCursorParam(e.target.value)} placeholder="Cursor param name (e.g. cursor)"
                          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                        <input required value={paginationNextCursorField} onChange={(e) => setPaginationNextCursorField(e.target.value)} placeholder="Next-cursor field in response (e.g. next_cursor or meta.next_cursor)"
                          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      </div>
                    )}
                  </div>
                )}
              </div>
              <div className="md:col-span-3 flex gap-2">
                <button type="button" onClick={() => setApiAuthMethod('headers')}
                  className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${apiAuthMethod === 'headers' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                  Auth headers
                </button>
                <button type="button" onClick={() => setApiAuthMethod('oauth2')}
                  className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${apiAuthMethod === 'oauth2' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                  OAuth2 (client credentials)
                </button>
              </div>
              {apiAuthMethod === 'headers' ? (
                <div className="md:col-span-3 space-y-2">
                  <p className="text-[11px] text-slate-400">
                    Auth headers (optional - leave all blank for a public API with no auth; add one row per header, e.g. X-RapidAPI-Key + X-RapidAPI-Host)
                  </p>
                  {authHeaderRows.map((row, index) => (
                    <div key={index} className="flex gap-2">
                      <input value={row.key} onChange={(e) => updateAuthHeaderRow(index, 'key', e.target.value)} placeholder="Header name (e.g. Authorization)"
                        className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input type="password" value={row.value} onChange={(e) => updateAuthHeaderRow(index, 'value', e.target.value)} placeholder="Value (e.g. Bearer ...)"
                        className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <button type="button" onClick={() => removeAuthHeaderRow(index)} disabled={authHeaderRows.length === 1}
                        aria-label="Remove header"
                        className="shrink-0 rounded-lg border border-slate-700 px-2 text-slate-400 hover:bg-slate-800 disabled:opacity-30">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                  <button type="button" onClick={addAuthHeaderRow}
                    className="text-[11px] font-semibold text-cyan-400 hover:text-cyan-300">
                    + Add header
                  </button>
                </div>
              ) : (
                <div className="md:col-span-3 grid gap-2 md:grid-cols-2">
                  <input required value={oauth2TokenUrl} onChange={(e) => setOauth2TokenUrl(e.target.value)} placeholder="Token URL (e.g. https://provider.com/oauth/token)"
                    className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                  <input required value={oauth2ClientId} onChange={(e) => setOauth2ClientId(e.target.value)} placeholder="Client ID"
                    className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                  <input required type="password" value={oauth2ClientSecret} onChange={(e) => setOauth2ClientSecret(e.target.value)} placeholder="Client secret"
                    className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                  <input value={oauth2Scope} onChange={(e) => setOauth2Scope(e.target.value)} placeholder="Scope (optional)"
                    className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                </div>
              )}
            </>
          ) : sourceType === 'redis' ? (
            <>
              <input required value={redisHost} onChange={(e) => setRedisHost(e.target.value)} placeholder="Host (e.g. 127.0.0.1)"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input required value={redisPort} onChange={(e) => setRedisPort(e.target.value)} placeholder="Port" inputMode="numeric"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input value={redisDb} onChange={(e) => setRedisDb(e.target.value)} placeholder="DB index (default: 0)" inputMode="numeric"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input value={redisUsername} onChange={(e) => setRedisUsername(e.target.value)} placeholder="Username (optional)"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <input type="password" value={redisPassword} onChange={(e) => setRedisPassword(e.target.value)} placeholder="Password (optional)"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-300">
                <input type="checkbox" checked={redisTls} onChange={(e) => setRedisTls(e.target.checked)} className="accent-cyan-500" />
                Use TLS (rediss://)
              </label>
            </>
          ) : sourceType === 'graphql' ? (
            <>
              <input required value={graphqlEndpoint} onChange={(e) => setGraphqlEndpoint(e.target.value)} placeholder="GraphQL endpoint (e.g. https://api.example.com/graphql)"
                className="md:col-span-3 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <div className="md:col-span-3 space-y-1">
                <p className="text-[11px] text-slate-400">Query</p>
                <textarea required value={graphqlQuery} onChange={(e) => setGraphqlQuery(e.target.value)} placeholder="{ users { id name email } }"
                  className="h-24 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              </div>
              <div className="md:col-span-3 space-y-1">
                <p className="text-[11px] text-slate-400">Variables (JSON, optional)</p>
                <textarea value={graphqlVariables} onChange={(e) => setGraphqlVariables(e.target.value)} placeholder='{"limit": 100}'
                  className="h-16 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              </div>
              <div className="md:col-span-3 flex gap-2">
                <button type="button" onClick={() => setApiAuthMethod('headers')}
                  className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${apiAuthMethod === 'headers' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                  Auth headers
                </button>
                <button type="button" onClick={() => setApiAuthMethod('oauth2')}
                  className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${apiAuthMethod === 'oauth2' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                  OAuth2 (client credentials)
                </button>
              </div>
              {apiAuthMethod === 'headers' ? (
                <div className="md:col-span-3 space-y-2">
                  <p className="text-[11px] text-slate-400">
                    Auth headers (optional - leave all blank for a public API with no auth; add one row per header)
                  </p>
                  {authHeaderRows.map((row, index) => (
                    <div key={index} className="flex gap-2">
                      <input value={row.key} onChange={(e) => updateAuthHeaderRow(index, 'key', e.target.value)} placeholder="Header name (e.g. Authorization)"
                        className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input type="password" value={row.value} onChange={(e) => updateAuthHeaderRow(index, 'value', e.target.value)} placeholder="Value (e.g. Bearer ...)"
                        className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <button type="button" onClick={() => removeAuthHeaderRow(index)} disabled={authHeaderRows.length === 1}
                        aria-label="Remove header"
                        className="shrink-0 rounded-lg border border-slate-700 px-2 text-slate-400 hover:bg-slate-800 disabled:opacity-30">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                  <button type="button" onClick={addAuthHeaderRow}
                    className="text-[11px] font-semibold text-cyan-400 hover:text-cyan-300">
                    + Add header
                  </button>
                </div>
              ) : (
                <div className="md:col-span-3 grid gap-2 md:grid-cols-2">
                  <input required value={oauth2TokenUrl} onChange={(e) => setOauth2TokenUrl(e.target.value)} placeholder="Token URL"
                    className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                  <input required value={oauth2ClientId} onChange={(e) => setOauth2ClientId(e.target.value)} placeholder="Client ID"
                    className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                  <input required type="password" value={oauth2ClientSecret} onChange={(e) => setOauth2ClientSecret(e.target.value)} placeholder="Client secret"
                    className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                  <input value={oauth2Scope} onChange={(e) => setOauth2Scope(e.target.value)} placeholder="Scope (optional)"
                    className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                </div>
              )}
            </>
          ) : sourceType === 'soap' ? (
            <>
              <input required value={soapEndpoint} onChange={(e) => setSoapEndpoint(e.target.value)} placeholder="SOAP endpoint (e.g. https://example.com/soap-service)"
                className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <div className="flex gap-2">
                <button type="button" onClick={() => setSoapVersion('1.1')}
                  className={`flex-1 rounded-lg px-3 py-2 text-xs font-semibold ${soapVersion === '1.1' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                  SOAP 1.1
                </button>
                <button type="button" onClick={() => setSoapVersion('1.2')}
                  className={`flex-1 rounded-lg px-3 py-2 text-xs font-semibold ${soapVersion === '1.2' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                  SOAP 1.2
                </button>
              </div>
              <input value={soapAction} onChange={(e) => setSoapAction(e.target.value)} placeholder="SOAPAction (optional)"
                className="md:col-span-3 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              <div className="md:col-span-3 space-y-1">
                <p className="text-[11px] text-slate-400">Request envelope (full XML, sent exactly as written)</p>
                <textarea required value={soapRequestBody} onChange={(e) => setSoapRequestBody(e.target.value)} placeholder='<soap:Envelope xmlns:soap="..."><soap:Body>...</soap:Body></soap:Envelope>'
                  className="h-28 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
              </div>
              <div className="md:col-span-3 flex gap-2">
                <button type="button" onClick={() => setApiAuthMethod('headers')}
                  className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${apiAuthMethod === 'headers' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                  Auth headers
                </button>
                <button type="button" onClick={() => setApiAuthMethod('oauth2')}
                  className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${apiAuthMethod === 'oauth2' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                  OAuth2 (client credentials)
                </button>
              </div>
              {apiAuthMethod === 'headers' ? (
                <div className="md:col-span-3 space-y-2">
                  <p className="text-[11px] text-slate-400">
                    Auth headers (optional - leave all blank for a service with no auth; add one row per header)
                  </p>
                  {authHeaderRows.map((row, index) => (
                    <div key={index} className="flex gap-2">
                      <input value={row.key} onChange={(e) => updateAuthHeaderRow(index, 'key', e.target.value)} placeholder="Header name (e.g. Authorization)"
                        className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input type="password" value={row.value} onChange={(e) => updateAuthHeaderRow(index, 'value', e.target.value)} placeholder="Value (e.g. Bearer ...)"
                        className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <button type="button" onClick={() => removeAuthHeaderRow(index)} disabled={authHeaderRows.length === 1}
                        aria-label="Remove header"
                        className="shrink-0 rounded-lg border border-slate-700 px-2 text-slate-400 hover:bg-slate-800 disabled:opacity-30">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                  <button type="button" onClick={addAuthHeaderRow}
                    className="text-[11px] font-semibold text-cyan-400 hover:text-cyan-300">
                    + Add header
                  </button>
                </div>
              ) : (
                <div className="md:col-span-3 grid gap-2 md:grid-cols-2">
                  <input required value={oauth2TokenUrl} onChange={(e) => setOauth2TokenUrl(e.target.value)} placeholder="Token URL"
                    className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                  <input required value={oauth2ClientId} onChange={(e) => setOauth2ClientId(e.target.value)} placeholder="Client ID"
                    className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                  <input required type="password" value={oauth2ClientSecret} onChange={(e) => setOauth2ClientSecret(e.target.value)} placeholder="Client secret"
                    className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                  <input value={oauth2Scope} onChange={(e) => setOauth2Scope(e.target.value)} placeholder="Scope (optional)"
                    className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                </div>
              )}
            </>
          ) : (
            <div className="md:col-span-3 space-y-2">
              <p className="text-[11px] text-slate-400">
                CSV or JSON files - one profile can hold several (e.g. orders.csv + customers.json for a dataset spanning multiple files)
              </p>
              <label className="flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-dashed border-slate-700 bg-slate-900 px-3 py-4 text-xs text-slate-400 hover:border-cyan-600 hover:text-cyan-300">
                <Upload className="h-4 w-4" />
                Choose files
                <input type="file" multiple accept=".csv,.json" onChange={onFilesSelected} className="hidden" />
              </label>
              {stagedFiles.length > 0 && (
                <ul className="space-y-1">
                  {stagedFiles.map((file, index) => (
                    <li key={`${file.name}-${index}`} className="flex items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-900 px-3 py-1.5 text-[11px] text-slate-300">
                      <span className="truncate">{file.name}</span>
                      <button type="button" onClick={() => removeStagedFile(index)} aria-label={`Remove ${file.name}`}
                        className="shrink-0 text-slate-500 hover:text-rose-400">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        <button type="submit" disabled={creating}
          className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-cyan-600 px-3 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50">
          {creating ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Plug className="h-3.5 w-3.5" />}
          Save profile
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

      <div className="space-y-2">
        {(() => {
          const visibleProfiles = filterType ? profiles.filter((p) => p.type === filterType) : profiles;
          return visibleProfiles.length === 0 ? (
          <p className="text-xs text-slate-500">No connection profiles yet.</p>
        ) : (
          visibleProfiles.map((profile) => (
            <div key={profile.id} className="rounded-lg border border-slate-800 bg-slate-950 p-3">
              <div className="flex items-center justify-between gap-3">
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
                    {DESTINATION_ONLY_TYPES.has(profile.type)
                      ? 'Destination-only - used to write pipeline output, not read as a source. No introspection needed.'
                      : profile.last_introspected_at
                      ? `Last introspected ${new Date(profile.last_introspected_at).toLocaleString()}`
                      : 'Not introspected yet - required before it can be used to generate a pipeline.'}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {!DESTINATION_ONLY_TYPES.has(profile.type) && (
                  <button onClick={() => introspect(profile.id)} disabled={busyId === profile.id}
                    className="rounded-lg border border-cyan-800 px-2.5 py-1.5 text-[11px] font-semibold text-cyan-300 hover:bg-cyan-950 disabled:opacity-50">
                    {profile.last_introspected_at ? 'Re-introspect' : 'Introspect'}
                  </button>
                  )}
                  {/* File profiles have no editable credentials dict at all
                      - their real data is uploaded files, a different
                      operation entirely - so no Edit button for them. */}
                  {profile.type !== 'file' && (
                    <button onClick={() => (editingId === profile.id ? cancelEdit() : startEdit(profile))} disabled={busyId === profile.id}
                      className="rounded-lg border border-slate-700 p-1.5 text-slate-400 hover:bg-slate-800 disabled:opacity-50"
                      aria-label={editingId === profile.id ? `Cancel editing ${profile.name}` : `Edit ${profile.name}`}>
                      {editingId === profile.id ? <X className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}
                    </button>
                  )}
                  {profile.type === 'file' && (
                    <button onClick={() => openFileManager(profile.id)} disabled={busyId === profile.id}
                      className="rounded-lg border border-slate-700 p-1.5 text-slate-400 hover:bg-slate-800 disabled:opacity-50"
                      aria-label={managingFilesId === profile.id ? `Close file manager for ${profile.name}` : `Manage files for ${profile.name}`}>
                      {managingFilesId === profile.id ? <X className="h-3.5 w-3.5" /> : <Upload className="h-3.5 w-3.5" />}
                    </button>
                  )}
                  <button onClick={() => remove(profile.id, profile.name)} disabled={busyId === profile.id}
                    className="rounded-lg border border-rose-800 p-1.5 text-rose-400 hover:bg-rose-950 disabled:opacity-50" aria-label={`Delete ${profile.name}`}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              {editingId === profile.id && (
                <div className="mt-3 space-y-2 border-t border-slate-800 pt-3">
                  <p className="text-[11px] text-slate-500">Only fields you fill in will change - everything left blank keeps its current value.</p>
                  <input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="Profile name"
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />

                  {profile.type === 'postgres' ? (
                    <div className="grid gap-2 md:grid-cols-3">
                      <input value={editHost} onChange={(e) => setEditHost(e.target.value)} placeholder="New host (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editPort} onChange={(e) => setEditPort(e.target.value)} placeholder="New port (optional)" inputMode="numeric"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editDatabase} onChange={(e) => setEditDatabase(e.target.value)} placeholder="New database (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editUsername} onChange={(e) => setEditUsername(e.target.value)} placeholder="New username (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input type="password" value={editPassword} onChange={(e) => setEditPassword(e.target.value)} placeholder="New password (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editSchema} onChange={(e) => setEditSchema(e.target.value)} placeholder="New schema (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                    </div>
                  ) : profile.type === 'azure_sql' ? (
                    <div className="grid gap-2 md:grid-cols-3">
                      <input value={editAzureSqlHost} onChange={(e) => setEditAzureSqlHost(e.target.value)} placeholder="New server (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editAzureSqlPort} onChange={(e) => setEditAzureSqlPort(e.target.value)} placeholder="New port (optional)" inputMode="numeric"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editAzureSqlDatabase} onChange={(e) => setEditAzureSqlDatabase(e.target.value)} placeholder="New database (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editAzureSqlUsername} onChange={(e) => setEditAzureSqlUsername(e.target.value)} placeholder="New username (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input type="password" value={editAzureSqlPassword} onChange={(e) => setEditAzureSqlPassword(e.target.value)} placeholder="New password (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editAzureSqlSchema} onChange={(e) => setEditAzureSqlSchema(e.target.value)} placeholder="New schema (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                    </div>
                  ) : profile.type === 'snowflake' ? (
                    <div className="grid gap-2 md:grid-cols-3">
                      <input value={editSnowflakeAccount} onChange={(e) => setEditSnowflakeAccount(e.target.value)} placeholder="New account identifier (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editSnowflakeDatabase} onChange={(e) => setEditSnowflakeDatabase(e.target.value)} placeholder="New database (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editSnowflakeUsername} onChange={(e) => setEditSnowflakeUsername(e.target.value)} placeholder="New username (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input type="password" value={editSnowflakePassword} onChange={(e) => setEditSnowflakePassword(e.target.value)} placeholder="New password (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editSnowflakeSchema} onChange={(e) => setEditSnowflakeSchema(e.target.value)} placeholder="New schema (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editSnowflakeWarehouse} onChange={(e) => setEditSnowflakeWarehouse(e.target.value)} placeholder="New warehouse (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editSnowflakeRole} onChange={(e) => setEditSnowflakeRole(e.target.value)} placeholder="New role (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                    </div>
                  ) : profile.type === 's3' ? (
                    <div className="grid gap-2 md:grid-cols-3">
                      <input value={editS3Bucket} onChange={(e) => setEditS3Bucket(e.target.value)} placeholder="New bucket (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editS3AccessKeyId} onChange={(e) => setEditS3AccessKeyId(e.target.value)} placeholder="New access key ID (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input type="password" value={editS3SecretAccessKey} onChange={(e) => setEditS3SecretAccessKey(e.target.value)} placeholder="New secret access key (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editS3Region} onChange={(e) => setEditS3Region(e.target.value)} placeholder="New region (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editS3EndpointUrl} onChange={(e) => setEditS3EndpointUrl(e.target.value)} placeholder="New endpoint URL (optional)"
                        className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editS3Prefix} onChange={(e) => setEditS3Prefix(e.target.value)} placeholder="New prefix (optional)"
                        className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                    </div>
                  ) : profile.type === 'azure_blob' ? (
                    <div className="grid gap-2 md:grid-cols-3">
                      <input value={editAzureBlobContainer} onChange={(e) => setEditAzureBlobContainer(e.target.value)} placeholder="New container (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editAzureBlobAccountName} onChange={(e) => setEditAzureBlobAccountName(e.target.value)} placeholder="New account name (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input type="password" value={editAzureBlobAccountKey} onChange={(e) => setEditAzureBlobAccountKey(e.target.value)} placeholder="New account key (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editAzureBlobPrefix} onChange={(e) => setEditAzureBlobPrefix(e.target.value)} placeholder="New prefix (optional)"
                        className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                    </div>
                  ) : profile.type === 'bigquery' ? (
                    <div className="space-y-2">
                      <textarea value={editBigqueryServiceAccountJson} onChange={(e) => setEditBigqueryServiceAccountJson(e.target.value)} placeholder="New service account key JSON (optional)"
                        className="h-28 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editBigqueryLocation} onChange={(e) => setEditBigqueryLocation(e.target.value)} placeholder="New location (optional)"
                        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                    </div>
                  ) : profile.type === 'redis' ? (
                    <div className="grid gap-2 md:grid-cols-3">
                      <input value={editRedisHost} onChange={(e) => setEditRedisHost(e.target.value)} placeholder="New host (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editRedisPort} onChange={(e) => setEditRedisPort(e.target.value)} placeholder="New port (optional)" inputMode="numeric"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editRedisDb} onChange={(e) => setEditRedisDb(e.target.value)} placeholder="New DB index (optional)" inputMode="numeric"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input value={editRedisUsername} onChange={(e) => setEditRedisUsername(e.target.value)} placeholder="New username (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <input type="password" value={editRedisPassword} onChange={(e) => setEditRedisPassword(e.target.value)} placeholder="New password (optional)"
                        className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                    </div>
                  ) : profile.type === 'graphql' ? (
                    <div className="space-y-2">
                      <input value={editGraphqlEndpoint} onChange={(e) => setEditGraphqlEndpoint(e.target.value)} placeholder="New endpoint (optional)"
                        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <textarea value={editGraphqlQuery} onChange={(e) => setEditGraphqlQuery(e.target.value)} placeholder="New query (optional)"
                        className="h-20 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <textarea value={editGraphqlVariables} onChange={(e) => setEditGraphqlVariables(e.target.value)} placeholder="New variables (JSON, optional)"
                        className="h-16 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <div className="space-y-2">
                        <div className="flex items-center gap-2">
                          <span className="text-[11px] text-slate-500">Authentication:</span>
                          <button type="button" onClick={() => setEditApiAuthMethod(null)}
                            className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editApiAuthMethod === null ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                            Unchanged
                          </button>
                          <button type="button" onClick={() => setEditApiAuthMethod('headers')}
                            className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editApiAuthMethod === 'headers' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                            Auth headers
                          </button>
                          <button type="button" onClick={() => setEditApiAuthMethod('oauth2')}
                            className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editApiAuthMethod === 'oauth2' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                            OAuth2
                          </button>
                        </div>
                        {editApiAuthMethod === 'headers' && (
                          <>
                            <p className="text-[11px] text-slate-500">
                              Auth headers below REPLACE the entire existing set - leave every row blank to keep current auth unchanged (or switch back to "Unchanged" above).
                            </p>
                            {editAuthHeaderRows.map((row, index) => (
                              <div key={index} className="flex gap-2">
                                <input value={row.key} onChange={(e) => updateEditAuthHeaderRow(index, 'key', e.target.value)} placeholder="Header name"
                                  className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                                <input type="password" value={row.value} onChange={(e) => updateEditAuthHeaderRow(index, 'value', e.target.value)} placeholder="Value"
                                  className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                                <button type="button" onClick={() => removeEditAuthHeaderRow(index)} disabled={editAuthHeaderRows.length === 1}
                                  aria-label="Remove header"
                                  className="shrink-0 rounded-lg border border-slate-700 px-2 text-slate-400 hover:bg-slate-800 disabled:opacity-30">
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              </div>
                            ))}
                            <button type="button" onClick={addEditAuthHeaderRow} className="text-[11px] font-semibold text-cyan-400 hover:text-cyan-300">
                              + Add header
                            </button>
                          </>
                        )}
                        {editApiAuthMethod === 'oauth2' && (
                          <div className="grid gap-2 md:grid-cols-2">
                            <input required value={editOauth2TokenUrl} onChange={(e) => setEditOauth2TokenUrl(e.target.value)} placeholder="Token URL"
                              className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                            <input required value={editOauth2ClientId} onChange={(e) => setEditOauth2ClientId(e.target.value)} placeholder="Client ID"
                              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                            <input required type="password" value={editOauth2ClientSecret} onChange={(e) => setEditOauth2ClientSecret(e.target.value)} placeholder="Client secret"
                              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                            <input value={editOauth2Scope} onChange={(e) => setEditOauth2Scope(e.target.value)} placeholder="Scope (optional)"
                              className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                          </div>
                        )}
                      </div>
                    </div>
                  ) : profile.type === 'soap' ? (
                    <div className="space-y-2">
                      <input value={editSoapEndpoint} onChange={(e) => setEditSoapEndpoint(e.target.value)} placeholder="New endpoint (optional)"
                        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] text-slate-500">Version:</span>
                        <button type="button" onClick={() => setEditSoapVersion(editSoapVersion === '1.1' ? null : '1.1')}
                          className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editSoapVersion === '1.1' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                          1.1
                        </button>
                        <button type="button" onClick={() => setEditSoapVersion(editSoapVersion === '1.2' ? null : '1.2')}
                          className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editSoapVersion === '1.2' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                          1.2
                        </button>
                        {editSoapVersion === null && <span className="text-[11px] text-slate-500">(unchanged)</span>}
                      </div>
                      <input value={editSoapAction} onChange={(e) => setEditSoapAction(e.target.value)} placeholder="New SOAPAction (optional)"
                        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <textarea value={editSoapRequestBody} onChange={(e) => setEditSoapRequestBody(e.target.value)} placeholder="New request envelope (XML, optional)"
                        className="h-24 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <div className="space-y-2">
                        <div className="flex items-center gap-2">
                          <span className="text-[11px] text-slate-500">Authentication:</span>
                          <button type="button" onClick={() => setEditApiAuthMethod(null)}
                            className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editApiAuthMethod === null ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                            Unchanged
                          </button>
                          <button type="button" onClick={() => setEditApiAuthMethod('headers')}
                            className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editApiAuthMethod === 'headers' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                            Auth headers
                          </button>
                          <button type="button" onClick={() => setEditApiAuthMethod('oauth2')}
                            className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editApiAuthMethod === 'oauth2' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                            OAuth2
                          </button>
                        </div>
                        {editApiAuthMethod === 'headers' && (
                          <>
                            <p className="text-[11px] text-slate-500">
                              Auth headers below REPLACE the entire existing set - leave every row blank to keep current auth unchanged (or switch back to "Unchanged" above).
                            </p>
                            {editAuthHeaderRows.map((row, index) => (
                              <div key={index} className="flex gap-2">
                                <input value={row.key} onChange={(e) => updateEditAuthHeaderRow(index, 'key', e.target.value)} placeholder="Header name"
                                  className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                                <input type="password" value={row.value} onChange={(e) => updateEditAuthHeaderRow(index, 'value', e.target.value)} placeholder="Value"
                                  className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                                <button type="button" onClick={() => removeEditAuthHeaderRow(index)} disabled={editAuthHeaderRows.length === 1}
                                  aria-label="Remove header"
                                  className="shrink-0 rounded-lg border border-slate-700 px-2 text-slate-400 hover:bg-slate-800 disabled:opacity-30">
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              </div>
                            ))}
                            <button type="button" onClick={addEditAuthHeaderRow} className="text-[11px] font-semibold text-cyan-400 hover:text-cyan-300">
                              + Add header
                            </button>
                          </>
                        )}
                        {editApiAuthMethod === 'oauth2' && (
                          <div className="grid gap-2 md:grid-cols-2">
                            <input required value={editOauth2TokenUrl} onChange={(e) => setEditOauth2TokenUrl(e.target.value)} placeholder="Token URL"
                              className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                            <input required value={editOauth2ClientId} onChange={(e) => setEditOauth2ClientId(e.target.value)} placeholder="Client ID"
                              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                            <input required type="password" value={editOauth2ClientSecret} onChange={(e) => setEditOauth2ClientSecret(e.target.value)} placeholder="Client secret"
                              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                            <input value={editOauth2Scope} onChange={(e) => setEditOauth2Scope(e.target.value)} placeholder="Scope (optional)"
                              className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                          </div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <input value={editBaseUrl} onChange={(e) => setEditBaseUrl(e.target.value)} placeholder="New base URL (optional)"
                        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] text-slate-500">Method:</span>
                        <button type="button" onClick={() => setEditApiMethod(editApiMethod === 'GET' ? null : 'GET')}
                          className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editApiMethod === 'GET' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                          GET
                        </button>
                        <button type="button" onClick={() => setEditApiMethod(editApiMethod === 'POST' ? null : 'POST')}
                          className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editApiMethod === 'POST' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                          POST
                        </button>
                        {editApiMethod === null && <span className="text-[11px] text-slate-500">(unchanged)</span>}
                      </div>
                      {editApiMethod === 'POST' && (
                        <div className="space-y-1">
                          <p className="text-[11px] text-slate-400">New request body (JSON, optional)</p>
                          <textarea value={editApiRequestBody} onChange={(e) => setEditApiRequestBody(e.target.value)} placeholder='{"query": "...", "page_size": 100}'
                            className="h-20 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                        </div>
                      )}
                      <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-950 p-3">
                        <label className="flex items-center gap-2 text-xs text-slate-300">
                          <input type="checkbox" checked={editPaginationChanged} onChange={(e) => setEditPaginationChanged(e.target.checked)} className="accent-cyan-500" />
                          Change pagination settings
                        </label>
                        {editPaginationChanged && (
                          <>
                            <p className="text-[11px] text-slate-500">This REPLACES the entire existing pagination config, not a partial update - fill in every field for the style you pick.</p>
                            <div className="flex gap-2">
                              <button type="button" onClick={() => setEditPaginationStyle('offset')}
                                className={`rounded-lg px-3 py-1.5 text-[11px] font-semibold ${editPaginationStyle === 'offset' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                                Offset/limit
                              </button>
                              <button type="button" onClick={() => setEditPaginationStyle('page')}
                                className={`rounded-lg px-3 py-1.5 text-[11px] font-semibold ${editPaginationStyle === 'page' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                                Page number
                              </button>
                              <button type="button" onClick={() => setEditPaginationStyle('cursor')}
                                className={`rounded-lg px-3 py-1.5 text-[11px] font-semibold ${editPaginationStyle === 'cursor' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                                Cursor
                              </button>
                            </div>
                            {editPaginationStyle === 'offset' && (
                              <div className="grid gap-2 md:grid-cols-3">
                                <input value={editPaginationOffsetParam} onChange={(e) => setEditPaginationOffsetParam(e.target.value)} placeholder="Offset param name"
                                  className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                                <input value={editPaginationLimitParam} onChange={(e) => setEditPaginationLimitParam(e.target.value)} placeholder="Limit param name"
                                  className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                                <input value={editPaginationPageSize} onChange={(e) => setEditPaginationPageSize(e.target.value)} placeholder="Records per page" inputMode="numeric"
                                  className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                              </div>
                            )}
                            {editPaginationStyle === 'page' && (
                              <div className="grid gap-2 md:grid-cols-3">
                                <input value={editPaginationPageParam} onChange={(e) => setEditPaginationPageParam(e.target.value)} placeholder="Page param name"
                                  className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                                <input value={editPaginationPageSizeParam} onChange={(e) => setEditPaginationPageSizeParam(e.target.value)} placeholder="Page size param (optional)"
                                  className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                                <input value={editPaginationPageSize} onChange={(e) => setEditPaginationPageSize(e.target.value)} placeholder="Records per page" inputMode="numeric"
                                  className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                              </div>
                            )}
                            {editPaginationStyle === 'cursor' && (
                              <div className="grid gap-2 md:grid-cols-2">
                                <input value={editPaginationCursorParam} onChange={(e) => setEditPaginationCursorParam(e.target.value)} placeholder="Cursor param name"
                                  className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                                <input required value={editPaginationNextCursorField} onChange={(e) => setEditPaginationNextCursorField(e.target.value)} placeholder="Next-cursor field (e.g. meta.next_cursor)"
                                  className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                              </div>
                            )}
                          </>
                        )}
                      </div>
                      <div className="space-y-2">
                        <div className="flex items-center gap-2">
                          <span className="text-[11px] text-slate-500">Authentication:</span>
                          <button type="button" onClick={() => setEditApiAuthMethod(null)}
                            className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editApiAuthMethod === null ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                            Unchanged
                          </button>
                          <button type="button" onClick={() => setEditApiAuthMethod('headers')}
                            className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editApiAuthMethod === 'headers' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                            Auth headers
                          </button>
                          <button type="button" onClick={() => setEditApiAuthMethod('oauth2')}
                            className={`rounded-lg px-3 py-1 text-[11px] font-semibold ${editApiAuthMethod === 'oauth2' ? 'bg-cyan-600 text-white' : 'border border-slate-700 text-slate-400 hover:bg-slate-800'}`}>
                            OAuth2
                          </button>
                        </div>
                        {editApiAuthMethod === 'headers' && (
                          <>
                            <p className="text-[11px] text-slate-500">
                              Auth headers below REPLACE the entire existing set - leave every row blank to keep current auth unchanged (or switch back to "Unchanged" above).
                            </p>
                            {editAuthHeaderRows.map((row, index) => (
                              <div key={index} className="flex gap-2">
                                <input value={row.key} onChange={(e) => updateEditAuthHeaderRow(index, 'key', e.target.value)} placeholder="Header name"
                                  className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                                <input type="password" value={row.value} onChange={(e) => updateEditAuthHeaderRow(index, 'value', e.target.value)} placeholder="Value"
                                  className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                                <button type="button" onClick={() => removeEditAuthHeaderRow(index)} disabled={editAuthHeaderRows.length === 1}
                                  aria-label="Remove header"
                                  className="shrink-0 rounded-lg border border-slate-700 px-2 text-slate-400 hover:bg-slate-800 disabled:opacity-30">
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              </div>
                            ))}
                            <button type="button" onClick={addEditAuthHeaderRow} className="text-[11px] font-semibold text-cyan-400 hover:text-cyan-300">
                              + Add header
                            </button>
                          </>
                        )}
                        {editApiAuthMethod === 'oauth2' && (
                          <div className="grid gap-2 md:grid-cols-2">
                            <input required value={editOauth2TokenUrl} onChange={(e) => setEditOauth2TokenUrl(e.target.value)} placeholder="Token URL"
                              className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                            <input required value={editOauth2ClientId} onChange={(e) => setEditOauth2ClientId(e.target.value)} placeholder="Client ID"
                              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                            <input required type="password" value={editOauth2ClientSecret} onChange={(e) => setEditOauth2ClientSecret(e.target.value)} placeholder="Client secret"
                              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                            <input value={editOauth2Scope} onChange={(e) => setEditOauth2Scope(e.target.value)} placeholder="Scope (optional)"
                              className="md:col-span-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-cyan-500 focus:outline-none" />
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  <div className="flex gap-2">
                    <button onClick={() => saveEdit(profile)} disabled={busyId === profile.id}
                      className="rounded-lg bg-cyan-600 px-3 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50">
                      Save changes
                    </button>
                    <button onClick={cancelEdit} disabled={busyId === profile.id}
                      className="rounded-lg border border-slate-700 px-3 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-800 disabled:opacity-50">
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              {managingFilesId === profile.id && (
                <div className="mt-3 space-y-2 border-t border-slate-800 pt-3">
                  {filesMessage && <p className="text-[11px] text-slate-400">{filesMessage}</p>}
                  {filesLoading && profileFiles.length === 0 ? (
                    <p className="text-[11px] text-slate-500">Loading files...</p>
                  ) : (
                    <div className="space-y-1.5">
                      {profileFiles.map((f) => (
                        <div key={f.id} className="flex items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-900 px-3 py-2">
                          <div className="min-w-0">
                            <p className="truncate text-xs text-slate-200">{f.original_filename}</p>
                            <p className="text-[10px] text-slate-500">{f.format.toUpperCase()}{f.size_bytes != null ? ` · ${(f.size_bytes / 1024).toFixed(1)} KB` : ''}</p>
                          </div>
                          <div className="flex shrink-0 items-center gap-1.5">
                            <label className="cursor-pointer rounded-lg border border-slate-700 px-2 py-1 text-[11px] font-semibold text-slate-300 hover:bg-slate-800">
                              Replace
                              <input type="file" accept=".csv,.json" className="hidden" disabled={filesLoading}
                                onChange={(e) => { if (e.target.files?.[0]) replaceFile(profile.id, f.id, e.target.files[0]); e.target.value = ''; }} />
                            </label>
                            <button onClick={() => removeFile(profile.id, f.id, f.original_filename)} disabled={filesLoading || profileFiles.length <= 1}
                              aria-label={`Remove ${f.original_filename}`}
                              className="rounded-lg border border-rose-800 p-1.5 text-rose-400 hover:bg-rose-950 disabled:opacity-30">
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-800">
                    <Upload className="h-3.5 w-3.5" /> Add files
                    <input type="file" accept=".csv,.json" multiple className="hidden" disabled={filesLoading}
                      onChange={(e) => { if (e.target.files) addFiles(profile.id, e.target.files); e.target.value = ''; }} />
                  </label>
                </div>
              )}
            </div>
          ))
        );
        })()}
      </div>
    </section>
  );
}
