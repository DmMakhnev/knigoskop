import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, errorMessage, request } from '../../api/auth';
import { messages } from '../../app/messages';
import { summaryMessages as t } from './messages';

interface Template {
  id: string;
  task_type: string;
  name: string;
  active_version_id: string | null;
}
interface Version {
  id: string;
  version: number;
  body: string;
  variables_schema: unknown;
  status: string;
}
interface Job {
  id: string;
  status: keyof typeof messages.ru.ai.states;
  progress: number;
  error_code: string | null;
}
const tasks = [
  'structure',
  'summary_detailed',
  'summary_short',
  'summary_medium',
  'validation',
];
const variablesSchema = {
  type: 'object',
  properties: {
    input: { type: 'string' },
    language: { type: 'string' },
    target_chars: { type: 'string' },
  },
  required: ['input', 'language', 'target_chars'],
  additionalProperties: false,
};

export function PromptAdmin() {
  const [selectedTemplate, setSelectedTemplate] = useState('');
  const [selectedVersion, setSelectedVersion] = useState('');
  const [offset, setOffset] = useState(0);
  const [body, setBody] = useState('INPUT_JSON:\n$input');
  const [task, setTask] = useState(tasks[0]);
  const [name, setName] = useState('');
  const [text, setText] = useState('');
  const [consent, setConsent] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<{ signature: string; key: string } | null>(null);
  const cache = useQueryClient();
  const templates = useQuery({
    queryKey: ['admin-prompts'],
    queryFn: () => request<{ items: Template[] }>('/admin/prompts'),
    retry: false,
  });
  const template =
    templates.data?.items.find((p) => p.id === selectedTemplate) ??
    templates.data?.items[0];
  const versions = useQuery({
    queryKey: ['admin-prompt-versions', template?.id, offset],
    queryFn: () =>
      request<{ items: Version[]; total: number }>(
        `/admin/prompts/${template?.id}/versions?offset=${offset}`,
      ),
    enabled: !!template,
    retry: false,
  });
  const version =
    versions.data?.items.find((v) => v.id === selectedVersion) ??
    versions.data?.items[0];
  const job = useQuery({
    queryKey: ['prompt-job', jobId],
    queryFn: () => request<Job>(`/jobs/${jobId}`),
    enabled: !!jobId,
    retry: false,
    refetchInterval: (q) =>
      !q.state.error &&
      (!q.state.data ||
        ['queued', 'running', 'validating'].includes(q.state.data.status))
        ? 1000
        : false,
  });
  const result = useQuery({
    queryKey: ['prompt-result', jobId],
    queryFn: () =>
      request<{
        content: unknown;
        provenance: { model_identifier: string; prompt_version: number };
      }>(`/jobs/${jobId}/prompt-result`),
    enabled: job.data?.status === 'completed',
    retry: false,
  });
  async function action(endpoint: string, payload?: unknown) {
    setBusy(true);
    setError('');
    try {
      await request(endpoint, 'POST', payload);
      await cache.invalidateQueries({ queryKey: ['admin-prompts'] });
      await cache.invalidateQueries({ queryKey: ['admin-prompt-versions'] });
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  async function test() {
    const signature = JSON.stringify({ version: version?.id, text, consent });
    if (pending.current?.signature !== signature)
      pending.current = { signature, key: crypto.randomUUID() };
    setBusy(true);
    setError('');
    try {
      const response = await request<Job>(
        `/admin/prompt-versions/${version?.id}/test`,
        'POST',
        {
          text,
          consent_external: consent,
          idempotency_key: pending.current.key,
        },
      );
      pending.current = null;
      setJobId(response.id);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status < 500)
        pending.current = null;
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  const active =
    !!jobId &&
    (!job.data ||
      ['queued', 'running', 'validating'].includes(job.data.status));
  return (
    <section className="prompt-admin" aria-label={t.promptsTitle}>
      <h2>{t.promptsTitle}</h2>
      {(error ||
        templates.error ||
        versions.error ||
        job.error ||
        result.error) && (
        <p role="alert">
          {error ||
            errorMessage(
              templates.error ?? versions.error ?? job.error ?? result.error,
            )}
        </p>
      )}
      <details>
        <summary>{t.createTemplate}</summary>
        <label>
          {t.task}
          <select value={task} onChange={(e) => setTask(e.target.value)}>
            {tasks.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          {t.name}
          <input
            value={name}
            maxLength={100}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <button
          disabled={busy || !name}
          onClick={() =>
            void action('/admin/prompts', { task_type: task, name })
          }
        >
          {t.createTemplate}
        </button>
      </details>
      <label>
        {t.task}
        <select
          value={template?.id ?? ''}
          onChange={(e) => {
            setSelectedTemplate(e.target.value);
            setSelectedVersion('');
            setOffset(0);
          }}
        >
          {templates.data?.items.map((p) => (
            <option key={p.id} value={p.id}>
              {p.task_type} · {p.name}
            </option>
          ))}
        </select>
      </label>
      {template && (
        <>
          <label>
            {t.versionLabel}
            <select
              value={version?.id ?? ''}
              onChange={(e) => {
                setSelectedVersion(e.target.value);
                const v = versions.data?.items.find(
                  (v) => v.id === e.target.value,
                );
                if (v) setBody(v.body);
              }}
            >
              {versions.data?.items.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.version} ·{' '}
                  {v.status === 'published' ? t.published : t.unpublished}
                  {v.id === template.active_version_id ? ` · ${t.active}` : ''}
                </option>
              ))}
            </select>
          </label>
          {version && (
            <details>
              <summary>
                {t.body} · {version.version}
              </summary>
              <pre>{version.body}</pre>
              <pre>{JSON.stringify(version.variables_schema, null, 2)}</pre>
              <button onClick={() => setBody(version.body)}>{t.refresh}</button>
            </details>
          )}
          <p>{t.variables}</p>
          <label>
            {t.body}
            <textarea
              rows={9}
              value={body}
              maxLength={8000}
              onChange={(e) => setBody(e.target.value)}
            />
          </label>
          <button
            disabled={busy || !body.includes('$input')}
            onClick={() =>
              void action(`/admin/prompts/${template.id}/versions`, {
                body,
                variables_schema: variablesSchema,
              })
            }
          >
            {t.draft}
          </button>
          <button
            disabled={busy || !version}
            onClick={() =>
              void action(`/admin/prompt-versions/${version?.id}/publish`)
            }
          >
            {t.publish}
          </button>
          <button
            disabled={busy || version?.status !== 'published'}
            onClick={() =>
              void action(`/admin/prompts/${template.id}/rollback`, {
                version_id: version?.id,
              })
            }
          >
            {t.rollback}
          </button>
          <nav aria-label={t.versionLabel}>
            <button
              disabled={offset === 0}
              onClick={() => {
                setOffset(Math.max(0, offset - 50));
                setSelectedVersion('');
              }}
            >
              {t.previous}
            </button>
            <button
              disabled={offset + 50 >= (versions.data?.total ?? 0)}
              onClick={() => {
                setOffset(offset + 50);
                setSelectedVersion('');
              }}
            >
              {t.next}
            </button>
          </nav>
          <p>{t.testNotice}</p>
          <label>
            {t.testText}
            <textarea
              value={text}
              maxLength={3000}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
            />
            {t.testConsent}
          </label>
          <button
            disabled={busy || active || !version || !text}
            onClick={() => void test()}
          >
            {t.test}
          </button>
          {job.data && (
            <p role="status">
              {messages.ru.ai.states[job.data.status]} · {job.data.progress}% ·{' '}
              {job.data.error_code}
            </p>
          )}
          {result.data && (
            <details open>
              <summary>{t.result}</summary>
              <p>
                {result.data.provenance.model_identifier} · {t.prompt}{' '}
                {result.data.provenance.prompt_version}
              </p>
              <pre>{JSON.stringify(result.data.content, null, 2)}</pre>
            </details>
          )}
        </>
      )}
    </section>
  );
}
