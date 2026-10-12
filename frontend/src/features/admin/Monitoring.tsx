import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { request, errorMessage } from "../../api/auth";
import { Pager } from "./Management";

type Page<T> = { items: T[]; total: number };
interface Statistics {
  users_total: number;
  jobs: Record<string, number>;
  llm: {
    calls: number;
    input_tokens: number;
    output_tokens: number;
    mean_latency_ms: number | null;
  };
  tts: { calls: number; chars: number; duration_ms: number };
  costs: {
    currency: string | null;
    estimated_cost_micros: number;
    unknown_cost_calls: number;
  }[];
  models: { model_id: string; calls: number }[];
  tts_profiles: { profile_id: string; calls: number }[];
}
const states: Record<string, string> = {
  queued: "В очереди",
  running: "Выполняется",
  validating: "Проверяется",
  completed: "Готово",
  failed: "Ошибка",
  cancelled: "Отменено",
};
function ErrorNotice({ error }: { error: unknown }) {
  return error ? <p role="alert">{errorMessage(error)}</p> : null;
}
export function StatisticsPanel() {
  const [days, setDays] = useState(7);
  const [user, setUser] = useState("");
  const [filter, setFilter] = useState("");
  const stats = useQuery({
    queryKey: ["admin-statistics", days, filter],
    queryFn: () =>
      request<Statistics>(
        `/admin/statistics?days=${days}${filter ? "&user_id=" + encodeURIComponent(filter) : ""}`,
      ),
    retry: false,
  });
  const s = stats.data;
  return (
    <section className="admin-section">
      <h2>Статистика и расходы</h2>
      <label>
        Период статистики
        <select
          aria-label="Период статистики"
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
        >
          {[1, 7, 30, 90].map((d) => (
            <option key={d} value={d}>
              {d} дней
            </option>
          ))}
        </select>
      </label>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setFilter(user);
        }}
      >
        <label>
          ID пользователя для статистики
          <input
            pattern="[0-9a-fA-F-]{36}"
            value={user}
            onChange={(e) => setUser(e.target.value)}
          />
        </label>
        <button>Применить фильтр</button>
      </form>
      <button onClick={() => void stats.refetch()}>Обновить статистику</button>
      <ErrorNotice error={stats.error} />
      {stats.isPending && <p role="status">Загрузка статистики…</p>}
      {s && (
        <>
          <p>
            Всего пользователей: {s.users_total}. Оценка расходов не является
            выпиской провайдера. Неизвестная стоимость не считается нулевой.
          </p>
          <dl>
            <dt>Запросы LLM</dt>
            <dd>{s.llm.calls}</dd>
            <dt>Входные / выходные токены</dt>
            <dd>
              {s.llm.input_tokens} / {s.llm.output_tokens}
            </dd>
            <dt>Средняя задержка LLM, мс</dt>
            <dd>{s.llm.mean_latency_ms ?? "Нет данных"}</dd>
            <dt>Запросы TTS</dt>
            <dd>{s.tts.calls}</dd>
            <dt>Символы TTS</dt>
            <dd>{s.tts.chars}</dd>
            <dt>Длительность TTS, секунды</dt>
            <dd>{s.tts.duration_ms / 1000}</dd>
          </dl>
          <ul>
            {Object.entries(s.jobs).map(([status, count]) => (
              <li key={status}>
                {states[status] || status}: {count}
              </li>
            ))}
          </ul>
          <div className="admin-table">
            <table>
              <caption>Оценка стоимости отдельно по валютам</caption>
              <thead>
                <tr>
                  <th>Валюта</th>
                  <th>Учтённая оценка</th>
                  <th>Вызовы с неизвестной стоимостью</th>
                </tr>
              </thead>
              <tbody>
                {s.costs.map((c) => (
                  <tr key={c.currency || "unknown"}>
                    <td>{c.currency || "Не указана"}</td>
                    <td>{(c.estimated_cost_micros / 1e6).toFixed(6)}</td>
                    <td>{c.unknown_cost_calls}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details>
            <summary>Вызовы по моделям и профилям</summary>
            <ul>
              {s.models.map((m) => (
                <li key={m.model_id}>
                  Модель {m.model_id}: {m.calls}
                </li>
              ))}
              {s.tts_profiles.map((p) => (
                <li key={p.profile_id}>
                  Профиль TTS {p.profile_id}: {p.calls}
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </section>
  );
}
interface Job {
  id: string;
  user_id: string;
  type: string;
  status: string;
  progress: number;
  attempts: number;
  error_code: string | null;
  created_at: string;
}
export function JobsPanel() {
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");
  const [errors, setErrors] = useState(false);
  const [pending, setPending] = useState<Job | null>(null);
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const jobs = useQuery({
    queryKey: ["admin-monitor", offset, status, errors],
    queryFn: () =>
      request<Page<Job>>(
        `/admin/job-monitor?offset=${offset}&limit=20&errors_only=${errors}${status ? "&status=" + status : ""}`,
      ),
    retry: false,
  });
  async function cancel() {
    if (!pending) return;
    setBusy(true);
    setFailure("");
    try {
      await request(`/admin/jobs/${pending.id}/cancel`, "POST");
      setPending(null);
      await jobs.refetch();
    } catch (e) {
      setFailure(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="admin-section">
      <h2>Задачи и ошибки</h2>
      <p>
        Содержимое книг, результаты, ключи и внутренние снимки настроек здесь не
        показываются. Отмена не возвращает уже списанные провайдером средства.
      </p>
      <label>
        Статус задачи
        <select
          aria-label="Статус задачи"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">Все статусы</option>
          {Object.entries(states).map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <label>
        <input
          type="checkbox"
          checked={errors}
          onChange={(e) => {
            setErrors(e.target.checked);
            setOffset(0);
          }}
        />
        Только задачи с кодом ошибки
      </label>
      <button onClick={() => void jobs.refetch()}>Обновить задачи</button>
      <ErrorNotice error={jobs.error} />
      {failure && <p role="alert">{failure}</p>}
      {pending && (
        <div role="group" aria-label="Подтверждение отмены">
          <p>Отменить задачу {pending.id}?</p>
          <button disabled={busy} onClick={() => void cancel()}>
            Подтвердить отмену
          </button>
          <button disabled={busy} onClick={() => setPending(null)}>
            Оставить задачу
          </button>
        </div>
      )}
      <ul>
        {jobs.data?.items.map((j) => (
          <li key={j.id}>
            <strong>{j.id}</strong> · пользователь {j.user_id} · {j.type} ·{" "}
            {states[j.status] || j.status} · {j.progress}% · попыток:{" "}
            {j.attempts}
            {j.error_code && <p>Код ошибки: {j.error_code}</p>}
            {["queued", "running", "validating"].includes(j.status) && (
              <button onClick={() => setPending(j)}>
                Отменить задачу {j.id}
              </button>
            )}
          </li>
        ))}
      </ul>
      <Pager
        offset={offset}
        total={jobs.data?.total || 0}
        onChange={setOffset}
      />
    </section>
  );
}
interface Usage {
  id: string;
  job_id: string;
  status: string;
  error_code?: string | null;
  attempt?: number;
  segment_index?: number;
  chars?: number;
  duration_ms?: number;
  latency_ms?: number | null;
  units?: { input_tokens?: number; output_tokens?: number };
  estimated_cost_micros: number | null;
  currency: string | null;
}
export function UsagePanel() {
  const [kind, setKind] = useState("llm");
  const [offset, setOffset] = useState(0);
  const endpoint = kind === "llm" ? "/admin/usage" : "/admin/audio/usage";
  const data = useQuery({
    queryKey: ["admin-usage", kind, offset],
    queryFn: () =>
      request<Page<Usage>>(`${endpoint}?offset=${offset}&limit=20`),
    retry: false,
  });
  return (
    <section className="admin-section">
      <h2>Журнал запросов</h2>
      <label>
        Вид запросов
        <select
          aria-label="Вид запросов"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value);
            setOffset(0);
          }}
        >
          <option value="llm">LLM</option>
          <option value="tts">TTS</option>
        </select>
      </label>
      <ErrorNotice error={data.error} />
      <ul>
        {data.data?.items.map((u) => (
          <li key={u.id}>
            Задача {u.job_id} · {u.status} ·{" "}
            {u.estimated_cost_micros === null
              ? "Стоимость неизвестна"
              : (u.estimated_cost_micros / 1e6).toFixed(6)}{" "}
            {u.currency || ""}
            {kind === "llm" ? (
              <p>
                Попытка {u.attempt} · токены {u.units?.input_tokens ?? "—"} /{" "}
                {u.units?.output_tokens ?? "—"} · задержка {u.latency_ms ?? "—"}{" "}
                мс · {u.error_code}
              </p>
            ) : (
              <p>
                Сегмент {u.segment_index} · символов {u.chars} · длительность{" "}
                {(u.duration_ms || 0) / 1000} с
              </p>
            )}
          </li>
        ))}
      </ul>
      <Pager
        offset={offset}
        total={data.data?.total || 0}
        onChange={setOffset}
      />
    </section>
  );
}
interface Audit {
  id: string;
  actor_id: string | null;
  action: string;
  entity_id: string;
  created_at: string;
}
export function AuditPanel() {
  const [offset, setOffset] = useState(0);
  const data = useQuery({
    queryKey: ["admin-audit", offset],
    queryFn: () =>
      request<Page<Audit>>(`/admin/audit?offset=${offset}&limit=20`),
    retry: false,
  });
  return (
    <section className="admin-section">
      <h2>Аудит изменений</h2>
      <p>
        Журнал содержит время, автора, действие и ID объекта. Значения ключей и
        тексты книг в него не записываются.
      </p>
      <ErrorNotice error={data.error} />
      <ul>
        {data.data?.items.map((e) => (
          <li key={e.id}>
            {new Date(e.created_at).toLocaleString("ru-RU")} · {e.action} ·
            автор {e.actor_id || "Аккаунт удалён"} · объект {e.entity_id}
          </li>
        ))}
      </ul>
      <Pager
        offset={offset}
        total={data.data?.total || 0}
        onChange={setOffset}
      />
    </section>
  );
}
