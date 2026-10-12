import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, errorMessage, request } from "../../api/auth";
import { activeMark, browserBoundary, segmentAt, unicodeSlice } from "./timing";
import type { Segment } from "./timing";

interface Profile {
  id: string;
  name: string;
  protocol: string;
  model: string;
  voices: string[];
  ready: boolean;
  external: boolean;
  currency: string | null;
}
interface Progress {
  revision: number;
  position_ms: number;
  playback_rate: number;
}
export interface Manifest {
  id: string;
  text: string;
  voice: string;
  duration_ms: number;
  segments: Segment[];
  demo: boolean;
  progress: Progress;
}
interface Job {
  id: string;
  status: string;
  error_code: string | null;
}

export function AudioPanel({
  itemId,
  summaryId,
  nodeId,
  text,
  complete,
}: {
  itemId: string;
  summaryId: string;
  nodeId?: string;
  text: string;
  complete: boolean;
}) {
  const profiles = useQuery({
    queryKey: ["audio-profiles"],
    queryFn: () => request<{ items: Profile[] }>("/audio/profiles"),
    retry: false,
  });
  const preferences = useQuery({
    queryKey: ["audio-settings"],
    queryFn: () =>
      request<{ profile_id: string | null; voice: string | null }>(
        "/me/audio-settings",
      ),
    retry: false,
  });
  const [profileId, setProfileId] = useState("");
  const profile =
    profiles.data?.items.find(
      (p) => p.id === (profileId || preferences.data?.profile_id),
    ) ?? profiles.data?.items[0];
  const [voice, setVoice] = useState("");
  const effectiveVoice = voice || preferences.data?.voice || "";
  const chosenVoice = profile?.voices.includes(effectiveVoice)
    ? effectiveVoice
    : profile?.voices[0];
  const [budget, setBudget] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [launch, setLaunch] = useState<{ job: Job; asset_id: string } | null>(
    null,
  );
  const pending = useRef<{ signature: string; key: string } | null>(null);
  const job = useQuery({
    queryKey: ["audio-job", launch?.job.id],
    queryFn: () => request<Job>(`/jobs/${launch?.job.id}`),
    enabled: !!launch,
    retry: false,
    refetchInterval: (q) =>
      !q.state.error &&
      (!q.state.data || ["queued", "running"].includes(q.state.data.status))
        ? 1000
        : false,
  });
  const manifest = useQuery({
    queryKey: ["audio-manifest", launch?.asset_id],
    queryFn: () => request<Manifest>(`/audio/${launch?.asset_id}/manifest`),
    enabled: job.data?.status === "completed",
    retry: false,
  });
  async function generate() {
    setBusy(true);
    setError("");
    const payload = {
      library_item_id: itemId,
      summary_id: summaryId,
      ...(nodeId ? { node_id: nodeId } : {}),
      profile_id: profile?.id,
      voice: chosenVoice,
      consent_external: consent,
      ...(profile?.external
        ? {
            max_cost_micros: Math.round(Number(budget) * 1_000_000),
            currency: profile.currency,
          }
        : {}),
    };
    const signature = JSON.stringify(payload);
    if (pending.current?.signature !== signature)
      pending.current = { signature, key: crypto.randomUUID() };
    try {
      const value = await request<{ job: Job; asset_id: string }>(
        "/audio/generate",
        "POST",
        { ...payload, idempotency_key: pending.current.key },
      );
      setLaunch(value);
      pending.current = null;
    } catch (failure) {
      if (failure instanceof ApiError && failure.status < 500)
        pending.current = null;
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  const running =
    busy ||
    (launch &&
      !job.error &&
      (!job.data || ["queued", "running"].includes(job.data.status)));
  return (
    <section className="audio-panel" aria-label="Озвучивание">
      <h3>Озвучивание</h3>
      <label>
        Модель озвучивания{" "}
        <select
          value={profile?.id ?? ""}
          onChange={(e) => {
            setProfileId(e.target.value);
            setVoice("");
          }}
        >
          {profiles.data?.items.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} · {p.model}
            </option>
          ))}
        </select>
      </label>
      <label>
        Голос{" "}
        <select
          value={chosenVoice ?? ""}
          onChange={(e) => setVoice(e.target.value)}
        >
          {profile?.voices.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      </label>
      <label>
        <input
          type="checkbox"
          checked={consent}
          onChange={(e) => setConsent(e.target.checked)}
        />
        Разрешаю передать резюме сервису озвучивания или голосовому сервису
        браузера
      </label>
      {profile?.external && (
        <label>
          Максимальный бюджет, {profile.currency ?? "валюта не настроена"}{" "}
          <input
            type="number"
            min="0"
            max="1000"
            step="0.01"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
        </label>
      )}
      <button
        disabled={
          !profile?.ready ||
          !chosenVoice ||
          !!running ||
          (profile.external &&
            (!consent ||
              budget === "" ||
              !profile.currency ||
              !Number.isFinite(Number(budget)) ||
              Number(budget) < 0))
        }
        onClick={() => void generate()}
      >
        Создать аудио
      </button>
      {!profile?.ready && (
        <p>
          Облачное озвучивание не настроено. Доступен резервный голос браузера.
        </p>
      )}
      {running && <p role="status">Аудио готовится в фоне…</p>}
      {job.data?.status === "failed" && (
        <p role="alert">
          Не удалось создать аудио: {job.data.error_code}. Повторный платный
          вызов автоматически не выполняется.
        </p>
      )}
      {(error || profiles.error || job.error || manifest.error) && (
        <p role="alert">
          {error || errorMessage(profiles.error ?? job.error ?? manifest.error)}
        </p>
      )}
      {manifest.data && (
        <MediaPlayer key={manifest.data.id} manifest={manifest.data} />
      )}
      <BrowserSpeech
        text={manifest.data?.text ?? text}
        consent={consent}
        complete={!!manifest.data || complete}
      />
    </section>
  );
}

export function MediaPlayer({ manifest }: { manifest: Manifest }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [index, setIndex] = useState(
    () =>
      segmentAt(manifest.segments, manifest.progress.position_ms)?.index ?? 0,
  );
  const [position, setPosition] = useState(manifest.progress.position_ms);
  const [rate, setRate] = useState(manifest.progress.playback_rate);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const [active, setActive] = useState<string | null>(null);
  const activeElement = useRef<HTMLButtonElement>(null);
  const revision = useRef(manifest.progress.revision);
  const saving = useRef(false);
  const queuedSave = useRef<{ ms: number; speed: number } | null>(null);
  const saveConflict = useRef(false);
  const lastSaved = useRef(-5000);
  const target = useRef(manifest.progress.position_ms);
  const shouldPlay = useRef(false);
  const segment = manifest.segments[index];
  useEffect(() => {
    if (audio.current) audio.current.playbackRate = rate;
  }, [rate]);
  useEffect(() => {
    if (autoScroll)
      activeElement.current?.scrollIntoView?.({ block: "nearest" });
  }, [active, autoScroll]);
  async function save(ms = position, speed = rate) {
    if (saveConflict.current) return;
    if (saving.current) {
      queuedSave.current = { ms, speed };
      return;
    }
    saving.current = true;
    try {
      const result = await request<Progress>("/me/audio-progress", "PUT", {
        asset_id: manifest.id,
        position_ms: Math.min(manifest.duration_ms, Math.round(ms)),
        playback_rate: speed,
        revision: revision.current,
      });
      revision.current = result.revision;
      lastSaved.current = ms;
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409)
        saveConflict.current = true;
      queuedSave.current = null;
      setError(errorMessage(failure));
    } finally {
      saving.current = false;
      const queued = queuedSave.current;
      queuedSave.current = null;
      if (queued) void save(queued.ms, queued.speed);
    }
  }
  function seek(ms: number) {
    const next = segmentAt(manifest.segments, ms);
    if (!next) return;
    target.current = ms;
    shouldPlay.current = playing;
    setPosition(ms);
    if (next.index !== index) setIndex(next.index);
    else if (audio.current) {
      audio.current.currentTime = (ms - next.start_ms) / 1000;
      update();
    }
    void save(ms);
  }
  function update() {
    if (!audio.current || !segment) return;
    const ms = Math.min(
      manifest.duration_ms,
      segment.start_ms + audio.current.currentTime * 1000,
    );
    setPosition(ms);
    const mark = activeMark(segment, audio.current.currentTime);
    setActive(mark ? `${index}:${mark.text_start}` : null);
    if (Math.abs(ms - lastSaved.current) >= 5000) void save(ms);
  }
  async function play() {
    try {
      await audio.current?.play();
      setPlaying(true);
    } catch {
      setError(
        "Браузер не смог воспроизвести аудио. Можно выбрать голос браузера.",
      );
    }
  }
  if (!segment) return <p role="alert">Нет аудиосегментов</p>;
  return (
    <section className="media-player" aria-label="Аудиоплеер">
      {manifest.demo && (
        <p>
          Демонстрационные сигналы с тестовыми таймкодами — это не озвученная
          речь.
        </p>
      )}
      {manifest.segments.some((s) => s.alignment !== "words") && (
        <p>Точные таймкоды недоступны. Подсветка слов отключена.</p>
      )}
      <audio
        ref={audio}
        src={segment.url}
        preload="metadata"
        onTimeUpdate={update}
        onSeeked={update}
        onPause={() => {
          setPlaying(false);
          void save();
        }}
        onPlay={() => setPlaying(true)}
        onError={() =>
          setError("Аудио недоступно. Попробуйте резервный голос браузера.")
        }
        onLoadedMetadata={() => {
          if (!audio.current) return;
          audio.current.playbackRate = rate;
          audio.current.currentTime = Math.max(
            0,
            (target.current - segment.start_ms) / 1000,
          );
          if (shouldPlay.current) void play();
        }}
        onEnded={() => {
          if (index + 1 < manifest.segments.length) {
            target.current = manifest.segments[index + 1].start_ms;
            shouldPlay.current = true;
            setIndex(index + 1);
          } else {
            setPlaying(false);
            setPosition(manifest.duration_ms);
            setActive(null);
            void save(manifest.duration_ms);
          }
        }}
      />
      <button
        onClick={() => {
          if (playing) audio.current?.pause();
          else void play();
        }}
      >
        {playing ? "Пауза" : "Воспроизвести"}
      </button>
      <label>
        Позиция аудио{" "}
        <input
          type="range"
          min="0"
          max={manifest.duration_ms}
          step="100"
          value={position}
          onChange={(e) => seek(Number(e.target.value))}
        />
      </label>
      <output>
        {Math.floor(position / 1000)} / {Math.ceil(manifest.duration_ms / 1000)}{" "}
        с
      </output>
      <label>
        Скорость{" "}
        <select
          aria-label="Скорость"
          value={rate}
          onChange={(e) => {
            const speed = Number(e.target.value);
            setRate(speed);
            void save(position, speed);
          }}
        >
          {[0.75, 1, 1.25, 1.5, 1.75, 2].map((r) => (
            <option key={r} value={r}>
              {r}×
            </option>
          ))}
        </select>
      </label>
      <label>
        <input
          type="checkbox"
          checked={autoScroll}
          onChange={(e) => setAutoScroll(e.target.checked)}
        />
        Автопрокрутка
      </label>
      <div className="karaoke-text">
        {manifest.segments.map((s) =>
          s.alignment === "words" ? (
            <p key={s.index}>
              {s.timings.map((mark, i) => {
                const key = `${s.index}:${mark.text_start}`;
                const end =
                  s.timings[i + 1]?.text_start ?? s.text_end - s.text_start;
                return (
                  <button
                    key={key}
                    ref={active === key ? activeElement : undefined}
                    className={active === key ? "karaoke-active" : ""}
                    aria-current={active === key ? "true" : undefined}
                    onClick={() => seek(s.start_ms + mark.start_ms)}
                  >
                    {unicodeSlice(
                      manifest.text,
                      s.text_start + mark.text_start,
                      s.text_start + end,
                    )}
                  </button>
                );
              })}
            </p>
          ) : (
            <p key={s.index}>
              {unicodeSlice(manifest.text, s.text_start, s.text_end)}
            </p>
          ),
        )}
      </div>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

export function BrowserSpeech({
  text,
  consent,
  complete,
}: {
  text: string;
  consent: boolean;
  complete: boolean;
}) {
  const supported =
    typeof window.speechSynthesis !== "undefined" &&
    typeof window.SpeechSynthesisUtterance !== "undefined";
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [voice, setVoice] = useState("");
  const [rate, setRate] = useState(1);
  const [speaking, setSpeaking] = useState(false);
  const [paused, setPaused] = useState(false);
  const [boundary, setBoundary] = useState<number | null>(null);
  const [error, setError] = useState("");
  const utterance = useRef<SpeechSynthesisUtterance | null>(null);
  let utf16Offset = 0;
  let codepointOffset = 0;
  const tokens = text.split(/(\s+)/).map((part) => {
    const token = { part, start: utf16Offset, point: codepointOffset };
    utf16Offset += part.length;
    codepointOffset += Array.from(part).length;
    return token;
  });
  useEffect(() => {
    if (!supported) return;
    const refresh = () => setVoices(window.speechSynthesis.getVoices());
    refresh();
    window.speechSynthesis.addEventListener("voiceschanged", refresh);
    return () => {
      window.speechSynthesis.removeEventListener("voiceschanged", refresh);
      if (utterance.current) window.speechSynthesis.cancel();
    };
  }, [supported]);
  function speak(start = 0) {
    if (!supported || !consent || !complete) return;
    setError("");
    utterance.current = null;
    setBoundary(null);
    window.speechSynthesis.cancel();
    const value = new SpeechSynthesisUtterance(text.slice(start));
    value.voice = voices.find((v) => v.voiceURI === voice) ?? null;
    value.lang = value.voice?.lang ?? "ru-RU";
    value.rate = rate;
    value.onboundary = (e) => {
      if (utterance.current === value)
        setBoundary(browserBoundary(text, start + e.charIndex));
    };
    value.onend = () => {
      if (utterance.current !== value) return;
      utterance.current = null;
      setSpeaking(false);
      setPaused(false);
      setBoundary(null);
    };
    value.onerror = (event) => {
      if (utterance.current !== value) return;
      utterance.current = null;
      if (event.error !== "canceled" && event.error !== "interrupted")
        setError("Голос браузера недоступен.");
      setSpeaking(false);
      setPaused(false);
      setBoundary(null);
    };
    utterance.current = value;
    setSpeaking(true);
    setPaused(false);
    window.speechSynthesis.speak(value);
  }
  return (
    <section aria-label="Резервный голос браузера">
      <h4>Резервный голос браузера</h4>
      <p>
        Доступность зависит от браузера. Голосовой сервис может получать текст;
        точные таймкоды и синхронизация между устройствами не гарантируются.
      </p>
      {!supported ? (
        <p>Web Speech API не поддерживается.</p>
      ) : (
        <>
          <label>
            Голос браузера{" "}
            <select
              aria-label="Голос браузера"
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
            >
              <option value="">По умолчанию</option>
              {voices.map((v) => (
                <option key={v.voiceURI} value={v.voiceURI}>
                  {v.name} · {v.lang}
                </option>
              ))}
            </select>
          </label>
          <label>
            Скорость голоса браузера{" "}
            <select
              aria-label="Скорость голоса браузера"
              value={rate}
              disabled={speaking}
              onChange={(e) => setRate(Number(e.target.value))}
            >
              {[0.75, 1, 1.5, 2].map((r) => (
                <option key={r} value={r}>
                  {r}×
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={!consent || !complete || !text}
            onClick={() => speak()}
          >
            Озвучить в браузере
          </button>
          <button
            disabled={!speaking}
            onClick={() => {
              if (paused) window.speechSynthesis.resume();
              else window.speechSynthesis.pause();
              setPaused(!paused);
            }}
          >
            {paused ? "Продолжить голос браузера" : "Пауза голоса браузера"}
          </button>
          <button
            disabled={!speaking}
            onClick={() => {
              utterance.current = null;
              window.speechSynthesis.cancel();
              setSpeaking(false);
              setPaused(false);
              setBoundary(null);
            }}
          >
            Остановить голос браузера
          </button>
          {boundary === null && (
            <p>
              Подсветка появится только при событиях границ слов от браузера.
            </p>
          )}
          {complete && (
            <p className="karaoke-text">
              {tokens.map(({ part, start, point }, index) => {
                if (!part.trim()) return part;
                return (
                  <button
                    key={index}
                    className={
                      boundary !== null &&
                      point <= boundary &&
                      boundary < point + Array.from(part).length
                        ? "karaoke-active"
                        : ""
                    }
                    disabled={!consent}
                    onClick={() => speak(start)}
                  >
                    {part}
                  </button>
                );
              })}
            </p>
          )}
        </>
      )}
      {!complete && (
        <p>Загрузите все эпизоды для озвучивания полного резюме в браузере.</p>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
