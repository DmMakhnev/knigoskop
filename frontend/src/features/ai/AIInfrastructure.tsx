import { Button } from '@radix-ui/themes';
import { useQuery } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { ApiError, errorMessage, request } from '../../api/auth';
import { messages } from '../../app/messages';

const t = messages.ru.ai;
interface Job {
  id: string;
  status:
    'queued' | 'running' | 'validating' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  attempts: number;
  error_code: string | null;
}
interface Model {
  id: string;
  model_identifier: string;
  enabled: boolean;
}
interface Status {
  master_key_configured: boolean;
  environment_key_configured?: boolean;
  route_configured: boolean;
}

export function AIInfrastructure() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [offset, setOffset] = useState(0);
  const pendingRequest = useRef<{ endpoint: string; key: string } | null>(null);
  const status = useQuery({
    queryKey: ['admin-ai-status'],
    queryFn: () => request<Status>('/admin/ai-status'),
    retry: false,
  });
  const models = useQuery({
    queryKey: ['admin-ai-models', offset],
    queryFn: () =>
      request<{ items: Model[]; total: number }>(
        `/admin/models?offset=${offset}&limit=20`,
      ),
    retry: false,
  });
  const job = useQuery({
    queryKey: ['ai-job', jobId],
    queryFn: () => request<Job>(`/jobs/${jobId}`),
    enabled: jobId !== null,
    retry: false,
    refetchInterval: (query) =>
      !query.state.error &&
      (!query.state.data ||
        ['queued', 'running', 'validating'].includes(query.state.data.status))
        ? 1000
        : false,
  });
  async function run(modelId?: string) {
    setBusy(true);
    setError('');
    const endpoint = modelId
      ? `/admin/models/${modelId}/test`
      : '/jobs/diagnostic';
    if (pendingRequest.current?.endpoint !== endpoint) {
      pendingRequest.current = { endpoint, key: crypto.randomUUID() };
    }
    try {
      const result = await request<Job>(endpoint, 'POST', {
        idempotency_key: pendingRequest.current.key,
      });
      setJobId(result.id);
      pendingRequest.current = null;
    } catch (failure) {
      if (failure instanceof ApiError && failure.status < 500)
        pendingRequest.current = null;
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    setBusy(true);
    setError('');
    try {
      await request(`/jobs/${jobId}/cancel`, 'POST');
      await job.refetch();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  const active =
    jobId !== null &&
    (!job.data ||
      ['queued', 'running', 'validating'].includes(job.data.status));
  return (
    <section aria-labelledby="ai-infrastructure-title">
      <h2 id="ai-infrastructure-title">{t.title}</h2>
      <p>{t.notice}</p>
      {status.data && (
        <p>
          {status.data.environment_key_configured
            ? t.environmentReady
            : status.data.master_key_configured
              ? t.masterReady
              : t.masterMissing}
        </p>
      )}
      {(error || status.isError || models.isError || job.isError) && (
        <p role="alert">
          {error || errorMessage(status.error ?? models.error ?? job.error)}
        </p>
      )}
      <Button
        disabled={busy || active || !status.data?.route_configured}
        onClick={() => void run()}
      >
        {t.testRoute}
      </Button>
      {status.data && !status.data.route_configured && <p>{t.noRoute}</p>}
      <ul className="user-list">
        {models.data?.items.map((model) => (
          <li key={model.id}>
            <span>{model.model_identifier}</span>
            <Button
              disabled={busy || active || !model.enabled}
              onClick={() => void run(model.id)}
            >
              {t.testModel}
              <span className="sr-only"> {model.model_identifier}</span>
            </Button>
          </li>
        ))}
      </ul>
      {models.data && models.data.total > 20 && (
        <nav aria-label={t.modelPages} className="pagination">
          <Button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            {t.previous}
          </Button>
          <Button
            disabled={offset + 20 >= models.data.total}
            onClick={() => setOffset(offset + 20)}
          >
            {t.next}
          </Button>
        </nav>
      )}
      {job.data && (
        <div role="status" aria-live="polite">
          <p>
            {t.states[job.data.status]} · {job.data.progress}% · {t.attempts}:{' '}
            {job.data.attempts}
          </p>
          <progress
            max={100}
            value={job.data.progress}
            aria-label={t.progress}
          />
          {job.data.error_code && (
            <p>
              {t.jobError}: {job.data.error_code}
            </p>
          )}
          {active && (
            <Button disabled={busy} onClick={() => void cancel()}>
              {t.cancel}
            </Button>
          )}
        </div>
      )}
      {jobId && job.isError && (
        <Button onClick={() => void job.refetch()}>{t.retry}</Button>
      )}
    </section>
  );
}
