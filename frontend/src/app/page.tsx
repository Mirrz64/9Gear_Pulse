'use client';

import React, { useState, useEffect, Fragment } from 'react';
import { Play, Database, Activity, RefreshCw, CheckCircle, AlertCircle, ChevronDown, ChevronUp, Terminal, Table, X, Code, Eye, EyeOff } from 'lucide-react';

interface AuditLog {
  id: number;
  pipeline_name: string;
  status: string;
  attempts: number;
  execution_time: string;
  logs: string;
}

interface ColumnSchema {
  column_name: string;
  data_type: string;
}

type SchemaMap = Record<string, ColumnSchema[]>;

export default function Dashboard() {
  const [goal, setGoal] = useState('');
  const [loading, setLoading] = useState(false);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [schema, setSchema] = useState<SchemaMap>({});
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [expandedLogId, setExpandedLogId] = useState<number | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [hideDltTables, setHideDltTables] = useState(true);

  const fetchLogs = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8000/api/logs');
      if (res.ok) {
        const data = await res.json();
        setLogs(data.logs || []);
      }
    } catch (err) {
      console.error('Failed to fetch logs:', err);
    }
  };

  const fetchSchema = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8000/api/schema');
      if (res.ok) {
        const data = await res.json();
        setSchema(data.schema || {});
      }
    } catch (err) {
      console.error('Failed to fetch schema:', err);
    }
  };

  useEffect(() => {
    fetchLogs();
    fetchSchema();
  }, []);

  const handleRunPipeline = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!goal.trim()) return;

    setLoading(true);
    setStatusMessage('Generating code & executing in Docker sandbox...');

    try {
      const res = await fetch('http://127.0.0.1:8000/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal, max_retries: 3 }),
      });

      if (res.ok) {
        setStatusMessage('Pipeline executed successfully!');
        setGoal('');
        fetchLogs();
        fetchSchema();
      } else {
        const err = await res.json();
        setStatusMessage(`Execution failed: ${err.detail || 'Unknown error'}`);
      }
    } catch (err) {
      setStatusMessage('Failed to connect to FastAPI backend.');
    } finally {
      setLoading(false);
    }
  };

  const toggleExpand = (id: number) => {
    setExpandedLogId(expandedLogId === id ? null : id);
  };

  const filteredSchema = Object.entries(schema).filter(([tableName]) => {
    if (hideDltTables) {
      return !tableName.includes('_dlt_') && !tableName.startsWith('_pipeline_audit');
    }
    return true;
  });

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 p-8 font-sans relative overflow-x-hidden">
      <div className="max-w-6xl mx-auto space-y-8">
        {/* Header */}
        <header className="flex items-center justify-between border-b border-slate-800 pb-6">
          <div className="flex items-center space-x-3">
            <Activity className="h-8 w-8 text-blue-500" />
            <div>
              <h1 className="text-2xl font-bold tracking-tight">9Gear Pulse</h1>
              <p className="text-xs text-slate-400">Autonomous Data Engine Control Plane</p>
            </div>
          </div>
          <div className="flex items-center space-x-3">
            <button
              onClick={() => setIsDrawerOpen(true)}
              className="flex items-center gap-2 bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 px-3 py-2 rounded-lg text-sm transition"
            >
              <Table className="h-4 w-4 text-blue-400" />
              View Database Schema
            </button>
            <button
              onClick={fetchLogs}
              className="p-2 text-slate-400 hover:text-slate-100 hover:bg-slate-900 rounded-lg transition"
              title="Refresh Audit Logs"
            >
              <RefreshCw className="h-5 w-5" />
            </button>
          </div>
        </header>

        {/* Input Card */}
        <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <Database className="h-5 w-5 text-blue-400" />
            Execute New Pipeline Goal
          </h2>
          <form onSubmit={handleRunPipeline} className="space-y-4">
            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="e.g. Extract all records from users table, append dynamic attributes, and load into analytics_reporting with auto schema evolution"
              className="w-full h-28 bg-slate-950 border border-slate-800 rounded-lg p-4 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500 transition resize-none"
              disabled={loading}
            />
            <div className="flex items-center justify-between">
              {statusMessage && (
                <p className={`text-xs ${statusMessage.includes('failed') ? 'text-red-400' : 'text-blue-400'}`}>
                  {statusMessage}
                </p>
              )}
              <button
                type="submit"
                disabled={loading || !goal.trim()}
                className="ml-auto bg-blue-600 hover:bg-blue-500 disabled:bg-slate-800 text-white font-medium px-5 py-2.5 rounded-lg flex items-center gap-2 text-sm transition"
              >
                {loading ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4 fill-current" />}
                {loading ? 'Orchestrating...' : 'Run Pipeline'}
              </button>
            </div>
          </form>
        </section>

        {/* Execution Audit Table */}
        <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
          <h2 className="text-lg font-semibold mb-4">Execution Audit Logs</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-400">
              <thead className="bg-slate-950 text-slate-300 uppercase text-xs border-b border-slate-800">
                <tr>
                  <th className="p-3">ID</th>
                  <th className="p-3">Pipeline Name</th>
                  <th className="p-3">Status</th>
                  <th className="p-3">Attempts</th>
                  <th className="p-3">Execution Time</th>
                  <th className="p-3 text-right">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {logs.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="p-4 text-center text-slate-500">
                      No audit records found. Trigger a pipeline to begin logging.
                    </td>
                  </tr>
                ) : (
                  logs.map((log) => (
                    <Fragment key={log.id}>
                      <tr
                        onClick={() => toggleExpand(log.id)}
                        className="hover:bg-slate-800/50 cursor-pointer transition"
                      >
                        <td className="p-3 font-mono text-xs">{log.id}</td>
                        <td className="p-3 font-medium text-slate-200">{log.pipeline_name}</td>
                        <td className="p-3">
                          <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold ${log.status === 'SUCCESS' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-rose-950 text-rose-400 border border-rose-800'
                            }`}>
                            {log.status === 'SUCCESS' ? <CheckCircle className="h-3 w-3" /> : <AlertCircle className="h-3 w-3" />}
                            {log.status}
                          </span>
                        </td>
                        <td className="p-3 font-mono text-xs">{log.attempts}</td>
                        <td className="p-3 text-xs">{new Date(log.execution_time).toLocaleString()}</td>
                        <td className="p-3 text-right">
                          <button className="text-slate-400 hover:text-slate-200 p-1">
                            {expandedLogId === log.id ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                          </button>
                        </td>
                      </tr>
                      {expandedLogId === log.id && (
                        <tr className="bg-slate-950/80 border-b border-slate-800">
                          <td colSpan={6} className="p-4">
                            <div className="space-y-2">
                              <div className="flex items-center gap-2 text-xs font-semibold text-slate-300">
                                <Terminal className="h-4 w-4 text-blue-400" />
                                Execution & Self-Healing Terminal Logs
                              </div>
                              <pre className="p-3 bg-slate-900 border border-slate-800 rounded-lg text-xs font-mono text-slate-300 overflow-x-auto whitespace-pre-wrap max-h-60">
                                {log.logs || 'No log details recorded for this run.'}
                              </pre>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {/* Schema Side Drawer */}
      {isDrawerOpen && (
        <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/60 backdrop-blur-sm">
          <div className="w-full max-w-md bg-slate-900 border-l border-slate-800 h-full p-6 shadow-2xl flex flex-col space-y-6">
            <div className="flex items-center justify-between border-b border-slate-800 pb-4">
              <div className="flex items-center gap-2">
                <Table className="h-5 w-5 text-blue-400" />
                <h3 className="text-lg font-semibold text-slate-100">Live Database Schema</h3>
              </div>
              <button
                onClick={() => setIsDrawerOpen(false)}
                className="p-1 text-slate-400 hover:text-slate-100 hover:bg-slate-800 rounded transition"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {/* Filter Toggle */}
            <div className="flex items-center justify-between bg-slate-950 p-3 rounded-lg border border-slate-800">
              <span className="text-xs text-slate-300 font-medium">Hide Internal System Tables</span>
              <button
                onClick={() => setHideDltTables(!hideDltTables)}
                className={`p-1.5 rounded-md border transition ${hideDltTables
                    ? 'bg-blue-950 border-blue-800 text-blue-400'
                    : 'bg-slate-900 border-slate-800 text-slate-500'
                  }`}
                title={hideDltTables ? "Showing Business Tables Only" : "Showing All System Tables"}
              >
                {hideDltTables ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>

            <div className="flex-1 overflow-y-auto space-y-6 pr-2">
              {filteredSchema.length === 0 ? (
                <p className="text-xs text-slate-500">No matching tables found in database.</p>
              ) : (
                filteredSchema.map(([tableName, columns]) => (
                  <div key={tableName} className="bg-slate-950 border border-slate-800 rounded-lg p-4 space-y-3">
                    <div className="font-mono text-sm font-semibold text-blue-400 border-b border-slate-800/60 pb-2">
                      {tableName}
                    </div>
                    <ul className="space-y-1.5 text-xs font-mono text-slate-300">
                      {columns.map((col, idx) => (
                        <li key={idx} className="flex justify-between items-center">
                          <span>{col.column_name}</span>
                          <span className="text-slate-500 text-[10px] bg-slate-900 px-2 py-0.5 rounded border border-slate-800">
                            {col.data_type}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </main>
  );
}