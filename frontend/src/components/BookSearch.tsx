import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@radix-ui/themes';
import { errorMessage, request } from '../api/auth';
import { messages } from '../app/messages';

const t = messages.ru.search;
interface Candidate {
  text_version_id?: string;
  source_id?: string;
  candidate_id?: string;
  fingerprint?: string;
  title: string;
  author: string;
  edition_label: string;
  translator: string;
  language: string;
  format: string;
  source_name: string;
  provenance: string;
  rights_status: string;
  can_import: boolean;
}
interface Results {
  items: Candidate[];
  total: number;
  source_errors: { source_id: string; message: string }[];
}
export function BookSearch() {
  const cache = useQueryClient();
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [localOffset, setLocalOffset] = useState(0);
  const [externalOffset, setExternalOffset] = useState(0);
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const local = useQuery({
    queryKey: ['book-search', query, 'local', localOffset],
    queryFn: () =>
      request<Results>(
        `/books/search?q=${encodeURIComponent(query)}&scope=local&offset=${localOffset}`,
      ),
    enabled: !!query,
    retry: false,
  });
  const external = useQuery({
    queryKey: ['book-search', query, 'external', externalOffset],
    queryFn: () =>
      request<Results>(
        `/books/search?q=${encodeURIComponent(query)}&scope=external&offset=${externalOffset}`,
      ),
    enabled: !!query && local.isSuccess,
    retry: false,
  });
  async function add() {
    if (!selected) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      if (selected.text_version_id) {
        await request('/library', 'POST', {
          text_version_id: selected.text_version_id,
        });
      } else {
        await request('/books/import', 'POST', {
          source_id: selected.source_id,
          candidate_id: selected.candidate_id,
          fingerprint: selected.fingerprint,
        });
      }
      await cache.invalidateQueries({ queryKey: ['library'] });
      await cache.invalidateQueries({
        queryKey: ['book-search', query, 'local'],
      });
      setSelected(null);
      setNotice(t.added);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="form-card" aria-label={t.title}>
      <h2>{t.title}</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(input.trim());
          setLocalOffset(0);
          setExternalOffset(0);
          setSelected(null);
          setNotice('');
          setError('');
        }}
      >
        <label>
          {t.query}
          <input
            value={input}
            maxLength={200}
            required
            onChange={(event) => setInput(event.target.value)}
            disabled={busy}
          />
        </label>
        <Button type="submit" disabled={busy || !input.trim()}>
          {t.find}
        </Button>
      </form>
      {!!query &&
        [
          {
            result: local,
            label: t.local,
            offset: localOffset,
            move: setLocalOffset,
          },
          {
            result: external,
            label: t.external,
            offset: externalOffset,
            move: setExternalOffset,
          },
        ].map(({ result, label, offset, move }) => (
          <div key={label}>
            <h3>{label}</h3>
            {result.isFetching && <p role="status">{t.loading}</p>}
            {result.isError && (
              <p role="alert">
                {errorMessage(result.error)}{' '}
                <Button onClick={() => void result.refetch()}>{t.retry}</Button>
              </p>
            )}
            {result.data?.total === 0 && <p>{t.empty}</p>}
            {result.data?.source_errors.map((item) => (
              <p role="status" key={item.source_id}>
                {item.message}
              </p>
            ))}
            <ul className="book-list">
              {result.data?.items.map((book) => (
                <li
                  className="book-card"
                  key={
                    book.text_version_id ??
                    `${book.source_id}:${book.candidate_id}:${book.fingerprint}`
                  }
                >
                  <h3>{book.title}</h3>
                  <p>
                    {book.author} · {book.edition_label} ·{' '}
                    {book.translator || t.noTranslator} · {book.language} ·{' '}
                    {book.format.toUpperCase()}
                  </p>
                  <p>
                    {book.source_name} ·{' '}
                    {t.rights[book.rights_status] ?? t.unverified}
                  </p>
                  <p>{book.provenance}</p>
                  <Button
                    variant="soft"
                    disabled={busy || !book.can_import}
                    onClick={() => {
                      setSelected(book);
                      setError('');
                    }}
                  >
                    {book.can_import ? t.choose : t.unverified}
                  </Button>
                </li>
              ))}
            </ul>
            {!!result.data?.total && (
              <nav aria-label={`${t.pagination}: ${label}`}>
                <Button
                  disabled={offset === 0 || busy}
                  onClick={() => move(Math.max(0, offset - 20))}
                >
                  {t.previous}
                </Button>
                <Button
                  disabled={offset + 20 >= result.data.total || busy}
                  onClick={() => move(offset + 20)}
                >
                  {t.next}
                </Button>
              </nav>
            )}
          </div>
        ))}
      {selected && (
        <div className="book-card" role="region" aria-label={t.confirm}>
          <h3>{t.confirm}</h3>
          <p>
            {selected.title} · {selected.author} · {selected.edition_label} ·{' '}
            {selected.translator || t.noTranslator}
          </p>
          <p>
            {selected.source_name} ·{' '}
            {t.rights[selected.rights_status] ?? t.unverified} ·{' '}
            {selected.provenance}
          </p>
          <Button disabled={busy} onClick={() => void add()}>
            {busy ? t.loading : t.add}
          </Button>{' '}
          <Button
            variant="soft"
            disabled={busy}
            onClick={() => setSelected(null)}
          >
            {t.cancel}
          </Button>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
