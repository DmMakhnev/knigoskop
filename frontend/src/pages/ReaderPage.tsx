import { ReaderSummary } from '../features/summaries/ReaderSummary';
import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, UIEvent } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { errorMessage, request } from '../api/auth';
import { messages } from '../app/messages';
import {
  paragraphs,
  selectionRange,
  splitHighlight,
} from '../features/reader/text';

const t = messages.ru.reader;
interface Section {
  id: string;
  text_version_id: string;
  parent_id: string | null;
  kind: string;
  title: string;
  start_offset: number;
  end_offset: number;
  order_index: number;
}
interface Position {
  text_version_id: string;
  section_id: string;
  offset: number;
  end_offset: number | null;
}
interface Progress {
  revision: number;
  position: Position | null;
}
interface Manifest {
  item: { title: string; author: string; edition_label: string };
  text_version_id: string;
  length: number;
  first_section_id: string;
  sections: Section[];
  total: number;
  progress: Progress;
}
interface Page {
  text_version_id: string;
  section_id: string;
  fragment: Section;
  text: string;
  start_offset: number;
  end_offset: number;
  highlight: { start_offset: number; end_offset: number } | null;
  previous_section_id: string | null;
  next_section_id: string | null;
}
interface Preferences {
  theme: 'light' | 'dark';
  font: 'serif' | 'sans' | 'mono';
  font_size: number;
  line_height: number;
  width: number;
}
interface Bookmark {
  id: string;
  section_id: string;
  text_version_id: string;
  start_offset: number;
  end_offset: number;
  label: string;
}
const defaults: Preferences = {
  theme: 'light',
  font: 'serif',
  font_size: 20,
  line_height: 1.7,
  width: 65,
};

export function ReaderPage() {
  const { itemId = '' } = useParams();
  const manifest = useQuery({
    queryKey: ['reader', itemId],
    queryFn: () => request<Manifest>(`/library/${itemId}/reader`),
    retry: false,
  });
  if (manifest.isPending) return <p role="status">{t.loading}</p>;
  if (manifest.isError)
    return (
      <p role="alert">
        {errorMessage(manifest.error)}{' '}
        <button onClick={() => void manifest.refetch()}>{t.retry}</button>
      </p>
    );
  return (
    <ReaderWorkspace key={itemId} itemId={itemId} manifest={manifest.data} />
  );
}

function ReaderWorkspace({
  itemId,
  manifest,
}: {
  itemId: string;
  manifest: Manifest;
}) {
  const cache = useQueryClient();
  const [params, setParams] = useSearchParams();
  const saved = manifest.progress.position;
  const [target, setTarget] = useState({
    id: params.get('section') ?? saved?.section_id ?? manifest.first_section_id,
    start: params.has('start') ? Number(params.get('start')) : saved?.offset,
    end: params.has('end')
      ? Number(params.get('end'))
      : (saved?.end_offset ?? undefined),
  });
  const [cursor, setCursor] = useState<number | null>(target.start ?? null);
  const [range, setRange] = useState<{
    start_offset: number;
    end_offset: number;
  } | null>(
    target.start !== undefined && target.end !== undefined
      ? { start_offset: target.start, end_offset: target.end }
      : null,
  );
  const [tab, setTab] = useState<'structure' | 'summary' | 'original'>(
    'original',
  );
  const [leftTab, setLeftTab] = useState<'structure' | 'summary'>('structure');
  const [treeOffset, setTreeOffset] = useState(0);
  const [bookmarkOffset, setBookmarkOffset] = useState(0);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [dirty, setDirty] = useState(false);
  const [preferences, setPreferences] = useState<Preferences>(defaults);
  const [preferencesDirty, setPreferencesDirty] = useState(false);
  const [bookmarkLabel, setBookmarkLabel] = useState('');
  const [fullscreen, setFullscreen] = useState(false);
  const revision = useRef(manifest.progress.revision);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const lastSaved = useRef('');
  const currentSignature = useRef('');
  const restoringScroll = useRef(false);
  const stopped = useRef(false);
  const content = useRef<HTMLDivElement>(null);
  const original = useRef<HTMLElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const page = useQuery({
    queryKey: ['reader-text', manifest.text_version_id, target],
    queryFn: () => {
      const query = new URLSearchParams();
      if (target.start !== undefined) query.set('start', String(target.start));
      if (target.end !== undefined) query.set('end', String(target.end));
      return request<Page>(
        `/texts/${manifest.text_version_id}/sections/${target.id}?${query}`,
      );
    },
    retry: false,
    enabled:
      !params.has('version') ||
      params.get('version') === manifest.text_version_id,
  });
  const storedPreferences = useQuery({
    queryKey: ['reading-settings'],
    queryFn: () => request<Preferences>('/me/reading-settings'),
    retry: false,
  });
  const tree = useQuery({
    queryKey: ['reader-tree', itemId, treeOffset],
    queryFn: () =>
      request<Manifest>(`/library/${itemId}/reader?offset=${treeOffset}`),
    initialData: treeOffset === 0 ? manifest : undefined,
  });
  const bookmarks = useQuery({
    queryKey: ['bookmarks', itemId, bookmarkOffset],
    queryFn: () =>
      request<{ items: Bookmark[]; total: number }>(
        `/library/${itemId}/bookmarks?offset=${bookmarkOffset}`,
      ),
  });
  useEffect(() => {
    if (storedPreferences.data && !preferencesDirty)
      setPreferences(storedPreferences.data);
  }, [storedPreferences.data, preferencesDirty]);
  const url = params.toString();
  useEffect(() => {
    if (!params.has('section')) return;
    setTarget({
      id: params.get('section')!,
      start: params.has('start') ? Number(params.get('start')) : undefined,
      end: params.has('end') ? Number(params.get('end')) : undefined,
    });
    setCursor(params.has('start') ? Number(params.get('start')) : null);
    setRange(
      params.has('start') && params.has('end')
        ? {
            start_offset: Number(params.get('start')),
            end_offset: Number(params.get('end')),
          }
        : null,
    );
    // The serialized URL represents browser back/forward changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);
  useEffect(() => {
    if (!page.data) return;
    const element = content.current;
    if (element) {
      const offset = target.start ?? page.data.start_offset;
      const candidates = Array.from(
        element.querySelectorAll<HTMLElement>('[data-offset]'),
      );
      const paragraph = [...candidates]
        .reverse()
        .find((node) => Number(node.dataset.offset) <= offset);
      restoringScroll.current = true;
      paragraph?.scrollIntoView?.({ block: 'start' });
      window.requestAnimationFrame(() =>
        window.requestAnimationFrame(() => {
          restoringScroll.current = false;
        }),
      );
    }
  }, [page.data, target.start]);
  const activePage = page.data;
  const position: Position | null = activePage
    ? {
        text_version_id: manifest.text_version_id,
        section_id: activePage.fragment.id,
        offset: Math.max(
          activePage.start_offset,
          Math.min(
            cursor ?? target.start ?? activePage.start_offset,
            activePage.end_offset - 1,
          ),
        ),
        end_offset: range
          ? Math.min(range.end_offset, activePage.end_offset)
          : null,
      }
    : null;
  if (
    position &&
    position.end_offset !== null &&
    position.end_offset <= position.offset
  )
    position.end_offset = null;
  const serializedPosition = JSON.stringify(position);
  currentSignature.current = serializedPosition;
  function save(positionToSave: Position | null) {
    if (!positionToSave || stopped.current) return Promise.resolve();
    const signature = JSON.stringify(positionToSave);
    queue.current = queue.current
      .catch(() => undefined)
      .then(async () => {
        if (lastSaved.current === signature || stopped.current) return;
        try {
          const result = await request<Progress>(
            '/me/reading-progress',
            'PUT',
            {
              library_item_id: itemId,
              ...positionToSave,
              revision: revision.current,
            },
          );
          revision.current = result.revision;
          lastSaved.current = signature;
          if (currentSignature.current === signature) {
            setNotice(t.saved);
            setError('');
            setDirty(false);
          }
        } catch (failure) {
          if ((failure as { code?: string }).code === 'progress_conflict')
            stopped.current = true;
          setError(errorMessage(failure));
          throw failure;
        }
      });
    return queue.current;
  }
  useEffect(() => {
    if (!dirty || !position) return;
    const timer = window.setTimeout(() => {
      void save(position).catch(() => undefined);
    }, 700);
    return () => window.clearTimeout(timer);
    // Saves are serialized through queue/revision refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serializedPosition, dirty]);
  useEffect(() => {
    function changed() {
      setFullscreen(!!document.fullscreenElement);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape' && document.fullscreenElement) {
        void document
          .exitFullscreen()
          .catch(() => setError(t.fullscreenUnavailable));
      }
    }
    document.addEventListener('fullscreenchange', changed);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('fullscreenchange', changed);
      document.removeEventListener('keydown', escape);
    };
  }, []);
  function go(id: string, start?: number, end?: number) {
    void save(position).catch(() => undefined);
    setTarget({ id, start, end });
    setCursor(start ?? null);
    setRange(
      start !== undefined && end !== undefined
        ? { start_offset: start, end_offset: end }
        : null,
    );
    const next = new URLSearchParams({
      version: manifest.text_version_id,
      section: id,
    });
    if (start !== undefined) next.set('start', String(start));
    if (end !== undefined) next.set('end', String(end));
    setParams(next);
    setTab('original');
    setDirty(true);
    setNotice('');
  }
  function scroll(event: UIEvent<HTMLElement>) {
    if (restoringScroll.current) return;
    const panel = event.currentTarget;
    const top = panel.getBoundingClientRect().top;
    const nodes = Array.from(
      panel.querySelectorAll<HTMLElement>('[data-offset]'),
    );
    const visible = nodes.find(
      (node) => node.getBoundingClientRect().bottom > top + 12,
    );
    if (visible) {
      setCursor(Number(visible.dataset.offset));
      setDirty(true);
    }
  }
  async function toggleFullscreen(panel: HTMLElement | null) {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (panel?.requestFullscreen) await panel.requestFullscreen();
      else setError(t.fullscreenUnavailable);
    } catch {
      setError(t.fullscreenUnavailable);
    }
  }
  async function saveSettings() {
    try {
      await request('/me/reading-settings', 'PUT', preferences);
      await cache.invalidateQueries({ queryKey: ['reading-settings'] });
      setPreferencesDirty(false);
      setNotice(t.settingsSaved);
      setError('');
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }
  async function addBookmark() {
    if (!position || !activePage) return;
    try {
      await request(`/library/${itemId}/bookmarks`, 'POST', {
        text_version_id: manifest.text_version_id,
        section_id: activePage.fragment.id,
        start_offset: position.offset,
        end_offset:
          position.end_offset ??
          Math.min(position.offset + 100, activePage.end_offset),
        label: bookmarkLabel || t.defaultBookmark,
      });
      setBookmarkLabel('');
      await cache.invalidateQueries({ queryKey: ['bookmarks', itemId] });
      setNotice(t.bookmarked);
      setError('');
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }
  function preference<K extends keyof Preferences>(
    key: K,
    value: Preferences[K],
  ) {
    setPreferences((prev) => ({ ...prev, [key]: value }));
    setPreferencesDirty(true);
  }
  if (
    params.has('version') &&
    params.get('version') !== manifest.text_version_id
  )
    return (
      <p role="alert">
        {t.wrongVersion}{' '}
        <Link to={`/library/${itemId}/read`}>{t.openBook}</Link>
      </p>
    );
  const invalidLink =
    (params.has('start') && !/^\d+$/.test(params.get('start')!)) ||
    (params.has('end') && !/^\d+$/.test(params.get('end')!)) ||
    (params.has('section') && !params.has('version'));
  if (invalidLink) return <p role="alert">{t.invalidLink}</p>;
  const font = {
    serif: 'Georgia, serif',
    sans: 'Arial, sans-serif',
    mono: 'monospace',
  }[preferences.font];
  return (
    <section
      className={`reader-shell reader-${preferences.theme}`}
      style={
        {
          '--reader-font': font,
          '--reader-size': `${preferences.font_size}px`,
          '--reader-line': preferences.line_height,
          '--reader-width': `${preferences.width}ch`,
        } as CSSProperties
      }
    >
      <header>
        <Link
          to="/library"
          onClick={() => void save(position).catch(() => undefined)}
        >
          {t.back}
        </Link>
        <h1>{manifest.item.title}</h1>
        <p>
          {manifest.item.author} · {manifest.item.edition_label}
        </p>
      </header>
      {error && (
        <p role="alert">
          {error}{' '}
          <button onClick={() => window.location.reload()}>{t.reload}</button>
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <details>
        <summary>{t.settings}</summary>
        <div className="reader-settings">
          <label>
            {t.theme}
            <select
              aria-label={t.theme}
              value={preferences.theme}
              onChange={(e) =>
                preference('theme', e.target.value as Preferences['theme'])
              }
            >
              <option value="light">{t.light}</option>
              <option value="dark">{t.dark}</option>
            </select>
          </label>
          <label>
            {t.font}
            <select
              aria-label={t.font}
              value={preferences.font}
              onChange={(e) =>
                preference('font', e.target.value as Preferences['font'])
              }
            >
              <option value="serif">{t.serif}</option>
              <option value="sans">{t.sans}</option>
              <option value="mono">{t.mono}</option>
            </select>
          </label>
          <label>
            {t.size}
            <input
              type="number"
              min={14}
              max={36}
              value={preferences.font_size}
              onChange={(e) => preference('font_size', Number(e.target.value))}
            />
          </label>
          <label>
            {t.line}
            <input
              type="number"
              min={1.2}
              max={2.5}
              step={0.1}
              value={preferences.line_height}
              onChange={(e) =>
                preference('line_height', Number(e.target.value))
              }
            />
          </label>
          <label>
            {t.width}
            <input
              type="number"
              min={40}
              max={100}
              value={preferences.width}
              onChange={(e) => preference('width', Number(e.target.value))}
            />
          </label>
          <button onClick={() => void saveSettings()}>{t.saveSettings}</button>
        </div>
        {storedPreferences.isError && (
          <p role="alert">{errorMessage(storedPreferences.error)}</p>
        )}
      </details>
      <div className="reader-tabs" role="tablist" aria-label={t.panels}>
        {(['structure', 'summary', 'original'] as const).map((name) => (
          <button
            key={name}
            role="tab"
            id={`tab-${name}`}
            aria-controls={`panel-${name}`}
            aria-selected={tab === name}
            tabIndex={tab === name ? 0 : -1}
            onKeyDown={(event) => {
              const names = ['structure', 'summary', 'original'] as const;
              const index = names.indexOf(name);
              const next =
                event.key === 'ArrowRight'
                  ? names[(index + 1) % 3]
                  : event.key === 'ArrowLeft'
                    ? names[(index + 2) % 3]
                    : event.key === 'Home'
                      ? names[0]
                      : event.key === 'End'
                        ? names[2]
                        : null;
              if (next) {
                event.preventDefault();
                setTab(next);
                document.getElementById(`tab-${next}`)?.focus();
              }
            }}
            onClick={() => setTab(name)}
          >
            {t[name]}
          </button>
        ))}
      </div>
      <div className="reader-panels">
        <aside
          ref={sidebar}
          className={`reader-panel reader-sidebar ${tab === 'original' ? 'mobile-hidden' : ''}`}
          aria-label={t.structure}
        >
          <div className="reader-desktop-tabs">
            <button
              aria-pressed={leftTab === 'structure'}
              onClick={() => setLeftTab('structure')}
            >
              {t.structure}
            </button>
            <button
              aria-pressed={leftTab === 'summary'}
              onClick={() => setLeftTab('summary')}
            >
              {t.summary}
            </button>
          </div>
          <button onClick={() => void toggleFullscreen(sidebar.current)}>
            {fullscreen ? t.exitFullscreen : t.fullscreenStructure}
          </button>
          <div
            id="panel-structure"
            hidden={
              tab === 'summary' || (tab === 'original' && leftTab === 'summary')
            }
          >
            <h2>{t.structure}</h2>
            {tree.isError && <p role="alert">{errorMessage(tree.error)}</p>}
            <ol className="reader-outline">
              {tree.data?.sections.map((section) => (
                <li key={section.id} className={`section-${section.kind}`}>
                  <button
                    aria-current={
                      activePage?.fragment.id === section.id
                        ? 'location'
                        : undefined
                    }
                    onClick={() => go(section.id, section.start_offset)}
                  >
                    {section.title}
                  </button>
                </li>
              ))}
            </ol>
            <nav aria-label={t.structurePages}>
              <button
                disabled={treeOffset === 0}
                onClick={() => setTreeOffset(Math.max(0, treeOffset - 200))}
              >
                {t.previous}
              </button>
              <button
                disabled={treeOffset + 200 >= (tree.data?.total ?? 0)}
                onClick={() => setTreeOffset(treeOffset + 200)}
              >
                {t.next}
              </button>
            </nav>
          </div>
          <div
            id="panel-summary"
            hidden={
              tab === 'structure' ||
              (tab === 'original' && leftTab === 'structure')
            }
          >
            <ReaderSummary
              itemId={itemId}
              versionId={manifest.text_version_id}
              onSource={(section, start, end) => {
                go(section, start, end);
                setTab('original');
              }}
            />
          </div>
          <h2>{t.bookmarks}</h2>
          <ul>
            {bookmarks.data?.items.map((bookmark) => (
              <li key={bookmark.id}>
                <button
                  onClick={() =>
                    go(
                      bookmark.section_id,
                      bookmark.start_offset,
                      bookmark.end_offset,
                    )
                  }
                >
                  {bookmark.label}
                </button>{' '}
                <button
                  aria-label={`${t.deleteBookmark}: ${bookmark.label}`}
                  onClick={() =>
                    void request(
                      `/library/${itemId}/bookmarks/${bookmark.id}`,
                      'DELETE',
                    )
                      .then(() =>
                        cache.invalidateQueries({
                          queryKey: ['bookmarks', itemId],
                        }),
                      )
                      .catch((failure) => setError(errorMessage(failure)))
                  }
                >
                  {t.deleteBookmark}
                </button>
              </li>
            ))}
          </ul>
          {bookmarks.isError && (
            <p role="alert">{errorMessage(bookmarks.error)}</p>
          )}
          <nav aria-label={t.bookmarkPages}>
            <button
              disabled={bookmarkOffset === 0}
              onClick={() =>
                setBookmarkOffset(Math.max(0, bookmarkOffset - 20))
              }
            >
              {t.previous}
            </button>
            <button
              disabled={bookmarkOffset + 20 >= (bookmarks.data?.total ?? 0)}
              onClick={() => setBookmarkOffset(bookmarkOffset + 20)}
            >
              {t.next}
            </button>
          </nav>
        </aside>
        <section
          ref={original}
          id="panel-original"
          className={`reader-panel reader-original ${tab !== 'original' ? 'mobile-hidden' : ''}`}
          aria-label={t.original}
        >
          <div className="reader-toolbar">
            <h2>{t.original}</h2>
            <button onClick={() => void toggleFullscreen(original.current)}>
              {fullscreen ? t.exitFullscreen : t.fullscreenOriginal}
            </button>
            <button
              disabled={!position}
              onClick={() => void save(position).catch(() => undefined)}
            >
              {t.savePosition}
            </button>
          </div>
          {page.isPending && <p role="status">{t.loading}</p>}
          {page.isError && (
            <p role="alert">
              {errorMessage(page.error)}{' '}
              <button onClick={() => void page.refetch()}>{t.retry}</button>
            </p>
          )}
          {activePage && (
            <>
              <p>
                {t.position}: {position?.offset ?? activePage.start_offset} /{' '}
                {manifest.length}
              </p>
              <div
                ref={content}
                tabIndex={0}
                className="reader-text"
                aria-label={t.text}
                onScroll={scroll}
                onMouseUp={() => {
                  if (content.current) {
                    const selected = selectionRange(content.current);
                    if (selected) {
                      setRange(selected);
                      setCursor(selected.start_offset);
                      setDirty(true);
                    }
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key === 'PageDown' && activePage.next_section_id) {
                    e.preventDefault();
                    go(activePage.next_section_id);
                  }
                  if (e.key === 'PageUp' && activePage.previous_section_id) {
                    e.preventDefault();
                    go(activePage.previous_section_id);
                  }
                }}
              >
                {paragraphs(activePage.text, activePage.start_offset).map(
                  (row) => {
                    const chunks = splitHighlight(row.text, row.start, range);
                    return (
                      <p
                        tabIndex={0}
                        key={row.start}
                        data-offset={row.start}
                        onFocus={() => {
                          setCursor(
                            Math.min(row.start, activePage.end_offset - 1),
                          );
                          setDirty(true);
                        }}
                        onClick={() => {
                          setCursor(
                            Math.min(row.start, activePage.end_offset - 1),
                          );
                          setDirty(true);
                        }}
                      >
                        {chunks.before}
                        {chunks.selected && <mark>{chunks.selected}</mark>}
                        {chunks.after}
                        {!row.text && <br />}
                      </p>
                    );
                  },
                )}
              </div>
              <nav aria-label={t.textPages}>
                <button
                  disabled={!activePage.previous_section_id}
                  onClick={() => go(activePage.previous_section_id!)}
                >
                  {t.previous}
                </button>
                <button
                  disabled={!activePage.next_section_id}
                  onClick={() => go(activePage.next_section_id!)}
                >
                  {t.next}
                </button>
              </nav>
              <label>
                {t.bookmarkName}
                <input
                  maxLength={100}
                  value={bookmarkLabel}
                  onChange={(e) => setBookmarkLabel(e.target.value)}
                />
              </label>
              <button onClick={() => void addBookmark()}>
                {t.addBookmark}
              </button>
            </>
          )}
        </section>
      </div>
    </section>
  );
}
