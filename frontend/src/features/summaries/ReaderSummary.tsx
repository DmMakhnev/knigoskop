import { AudioPanel } from "../audio/AudioPanel";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, errorMessage, request } from "../../api/auth";
import { messages } from "../../app/messages";
import { summaryMessages as t } from "./messages";

type Level = "short" | "medium" | "detailed";
interface Page<T> {
  items: T[];
  total: number;
}
interface Summary {
  id: string;
  generation_id: string;
  text_version_id: string;
  created_at: string;
  provenance: { mock: boolean };
}
interface Span {
  text_version_id: string;
  section_id: string;
  start_offset: number;
  end_offset: number;
}
interface Node {
  id: string;
  parent_id: string | null;
  kind: string;
  title: string;
  text: string;
  source_spans: Span[];
  provenance: {
    generation?: { model_identifier: string; prompt_version: number };
    model_identifier?: string;
    prompt_version?: number;
  };
}

function audioText(items: Node[], selected?: string): string {
  const included = new Set(selected ? [selected] : items.map((n) => n.id));
  if (selected) {
    while (true) {
      const children = items.filter(
        (n) => n.parent_id && included.has(n.parent_id) && !included.has(n.id),
      );
      if (!children.length) break;
      children.forEach((n) => included.add(n.id));
    }
  }
  const parents = new Set(
    items.filter((n) => included.has(n.id)).map((n) => n.parent_id),
  );
  return items
    .filter((n) => included.has(n.id) && !parents.has(n.id))
    .map((n) => n.text)
    .join("\n\n");
}

interface Check {
  step_index: number;
  findings: { findings: { severity: string; message: string }[] };
  provenance: {
    model_identifier: string;
    prompt_version: number;
    validation_round: number;
  };
}
interface Config {
  ready: boolean;
  external: boolean;
  models: string[];
  latest_job_id: string | null;
}
interface Job {
  id: string;
  status: keyof typeof messages.ru.ai.states;
  progress: number;
  error_code: string | null;
}

export function ReaderSummary({
  itemId,
  versionId,
  onSource,
}: {
  itemId: string;
  versionId: string;
  onSource: (section: string, start: number, end: number) => void;
}) {
  const [audioOpen, setAudioOpen] = useState(false);
  const [audioNode, setAudioNode] = useState<string | undefined>();
  const [level, setLevel] = useState<Level>("detailed");
  const [selected, setSelected] = useState("");
  const [offset, setOffset] = useState(0);
  const [nodeLimit, setNodeLimit] = useState(200);
  const [checkOffset, setCheckOffset] = useState(0);
  const [jobId, setJobId] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<{ signature: string; key: string } | null>(null);
  const cache = useQueryClient();
  const base = `/library/${itemId}`;
  const config = useQuery({
    queryKey: ["processing-config", itemId],
    queryFn: () => request<Config>(`${base}/processing-config`),
    retry: false,
  });
  const effectiveJob = jobId ?? config.data?.latest_job_id;
  const job = useQuery({
    queryKey: ["summary-job", effectiveJob],
    queryFn: () => request<Job>(`/jobs/${effectiveJob}`),
    enabled: !!effectiveJob,
    retry: false,
    refetchInterval: (q) =>
      !q.state.error &&
      (!q.state.data ||
        ["queued", "running", "validating"].includes(q.state.data.status))
        ? 1000
        : false,
  });
  useEffect(() => {
    if (job.data?.status === "completed") {
      void cache.invalidateQueries({ queryKey: ["summaries", itemId] });
    }
  }, [job.data?.id, job.data?.status, itemId, cache]);
  const versions = useQuery({
    queryKey: ["summaries", itemId, level, offset],
    queryFn: () =>
      request<Page<Summary>>(
        `${base}/summaries?level=${level}&offset=${offset}`,
      ),
    retry: false,
  });
  const summary =
    versions.data?.items.find((s) => s.id === selected) ??
    versions.data?.items[0];
  const nodes = useQuery({
    queryKey: ["summary-nodes", itemId, summary?.id, nodeLimit],
    queryFn: async () => {
      const items: Node[] = [];
      let total = 0;
      for (let pageOffset = 0; pageOffset < nodeLimit; pageOffset += 200) {
        const page = await request<Page<Node>>(
          `${base}/summaries/${summary?.id}/nodes?offset=${pageOffset}&limit=200`,
        );
        items.push(...page.items);
        total = page.total;
        if (items.length >= total) break;
      }
      return { items, total };
    },
    enabled: !!summary,
    retry: false,
  });
  const checks = useQuery({
    queryKey: ["summary-checks", itemId, summary?.id, checkOffset],
    queryFn: () =>
      request<Page<Check>>(
        `${base}/summaries/${summary?.id}/validations?offset=${checkOffset}`,
      ),
    enabled: !!summary,
    retry: false,
  });
  const failedChecks = useQuery({
    queryKey: ["failed-summary-checks", effectiveJob],
    queryFn: () =>
      request<Page<Check>>(`/jobs/${effectiveJob}/validation-findings`),
    enabled: job.data?.status === "failed",
    retry: false,
  });
  const active =
    !!effectiveJob &&
    (!job.data ||
      ["queued", "running", "validating"].includes(job.data.status));
  async function process(chapter?: string) {
    setBusy(true);
    setError("");
    const payload = {
      ...(chapter ? { chapter_id: chapter, base_summary_id: summary?.id } : {}),
      consent_external: consent,
    };
    const signature = JSON.stringify(payload);
    if (pending.current?.signature !== signature)
      pending.current = { signature, key: crypto.randomUUID() };
    try {
      const result = await request<Job>(`${base}/process`, "POST", {
        ...payload,
        idempotency_key: pending.current.key,
      });
      pending.current = null;
      setJobId(result.id);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status < 500)
        pending.current = null;
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    setBusy(true);
    try {
      await request(`/jobs/${effectiveJob}/cancel`, "POST");
      await job.refetch();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  const canProcess =
    config.data?.ready &&
    !busy &&
    !active &&
    (!config.data.external || consent);
  function renderNode(node: Node, depth = 0): React.ReactNode {
    if (depth > 30) return null;
    const children =
      nodes.data?.items.filter((child) => child.parent_id === node.id) ?? [];
    const provenance = node.provenance.generation ?? node.provenance;
    const content = (
      <>
        <p className="summary-text">{node.text}</p>
        <button
          onClick={() => {
            setAudioNode(node.id);
            setAudioOpen(true);
          }}
        >
          Озвучить этот фрагмент
        </button>
        {node.source_spans
          .filter((span) => span.text_version_id === versionId)
          .map((span) => (
            <button
              key={`${span.section_id}:${span.start_offset}`}
              onClick={() =>
                onSource(span.section_id, span.start_offset, span.end_offset)
              }
            >
              {t.source}
            </button>
          ))}
        {node.kind === "chapter" && (
          <button
            disabled={!canProcess}
            onClick={() => void process(node.source_spans[0]?.section_id)}
          >
            {t.regenerate} · {node.title}
          </button>
        )}
        <details>
          <summary>{t.provenance}</summary>
          <p>
            {provenance.model_identifier} · {t.prompt}{" "}
            {provenance.prompt_version}
          </p>
        </details>
        {children.map((child) => renderNode(child, depth + 1))}
      </>
    );
    return node.kind === "episode" ? (
      <article key={node.id}>
        <h3>{node.title}</h3>
        {content}
      </article>
    ) : (
      <details key={node.id} open={node.kind === "root"}>
        <summary>{node.title}</summary>
        {content}
      </details>
    );
  }
  function renderCheck(check: Check) {
    return (
      <li key={check.step_index}>
        {t.stage} {check.step_index} · {t.round}{" "}
        {check.provenance.validation_round} ·{" "}
        {check.provenance.model_identifier} · {t.prompt}{" "}
        {check.provenance.prompt_version}
        <ul>
          {check.findings.findings.map((finding, index) => (
            <li key={index}>
              {finding.severity === "error"
                ? messages.ru.ai.states.failed
                : t.findings}
              : {finding.message}
            </li>
          ))}
        </ul>
      </li>
    );
  }
  return (
    <section className="summary-workspace" aria-label={t.title}>
      <h2>{t.title}</h2>
      <p>{t.notice}</p>
      {config.data && (
        <>
          <p>{config.data.external ? t.external : t.mock}</p>
          <p>{config.data.models.join(", ")}</p>
          {config.data.external && (
            <label>
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
              />
              {t.consent}
            </label>
          )}
          {!config.data.ready && <p>{t.missing}</p>}
        </>
      )}
      <button disabled={!canProcess} onClick={() => void process()}>
        {t.process}
      </button>
      {job.data && (
        <p role="status">
          {messages.ru.ai.states[job.data.status]} · {job.data.progress}%
          {job.data.error_code ? ` · ${job.data.error_code}` : ""}
        </p>
      )}
      {active && (
        <button disabled={busy} onClick={() => void cancel()}>
          {t.cancel}
        </button>
      )}
      {(error ||
        config.error ||
        versions.error ||
        nodes.error ||
        checks.error ||
        job.error) && (
        <p role="alert">
          {error ||
            errorMessage(
              config.error ??
                versions.error ??
                nodes.error ??
                checks.error ??
                job.error,
            )}
        </p>
      )}
      <label>
        {t.title}
        <select
          value={level}
          onChange={(e) => {
            setAudioOpen(false);
            setLevel(e.target.value as Level);
            setSelected("");
            setOffset(0);
            setCheckOffset(0);
          }}
        >
          {Object.entries(t.levels).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {versions.isPending && <p>{t.loading}</p>}
      {versions.data?.items.length === 0 && <p>{t.empty}</p>}
      {summary && (
        <>
          <label>
            {t.version}
            <select
              value={summary.id}
              onChange={(e) => {
                setAudioOpen(false);
                setSelected(e.target.value);
                setCheckOffset(0);
              }}
            >
              {versions.data?.items.map((s) => (
                <option key={s.id} value={s.id}>
                  {new Date(s.created_at).toLocaleString("ru-RU")} ·{" "}
                  {s.generation_id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => {
              setAudioNode(undefined);
              setAudioOpen(true);
            }}
          >
            Озвучить резюме
          </button>
          {audioOpen && nodes.data && (
            <AudioPanel
              key={`${summary.id}:${audioNode ?? "all"}`}
              itemId={itemId}
              summaryId={summary.id}
              nodeId={audioNode}
              text={audioText(nodes.data.items, audioNode)}
              complete={nodes.data.items.length === nodes.data.total}
            />
          )}
          {summary.provenance.mock && <p>{t.mock}</p>}
          {nodes.data?.items
            .filter((n) => !n.parent_id)
            .map((n) => renderNode(n))}
          {nodes.data && nodes.data.total > nodeLimit && (
            <button onClick={() => setNodeLimit(nodeLimit + 200)}>
              {t.more}
            </button>
          )}
          <details>
            <summary>{t.check}</summary>
            <ul>{checks.data?.items.map(renderCheck)}</ul>
            <button
              disabled={checkOffset === 0}
              onClick={() => setCheckOffset(Math.max(0, checkOffset - 50))}
            >
              {t.previous}
            </button>
            <button
              disabled={checkOffset + 50 >= (checks.data?.total ?? 0)}
              onClick={() => setCheckOffset(checkOffset + 50)}
            >
              {t.next}
            </button>
          </details>
        </>
      )}
      <nav aria-label={t.version}>
        <button
          disabled={offset === 0}
          onClick={() => {
            setOffset(Math.max(0, offset - 20));
            setSelected("");
          }}
        >
          {t.previous}
        </button>
        <button
          disabled={offset + 20 >= (versions.data?.total ?? 0)}
          onClick={() => {
            setOffset(offset + 20);
            setSelected("");
          }}
        >
          {t.next}
        </button>
      </nav>
      {failedChecks.data && (
        <details>
          <summary>{t.findings}</summary>
          <ul>{failedChecks.data.items.map(renderCheck)}</ul>
        </details>
      )}
    </section>
  );
}
