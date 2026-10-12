import { Link } from 'react-router-dom';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@radix-ui/themes';
import { request, errorMessage } from '../api/auth';
import { BookSearch } from '../components/BookSearch';
import { messages } from '../app/messages';

const t = messages.ru.library;
interface Item {
  id: string;
  title: string;
  author: string;
  edition_label: string;
  translator: string;
  format: string;
  favorite: boolean;
  folder_id: string | null;
}
interface Folder {
  id: string;
  name: string;
}
interface PublicBook {
  text_version_id: string;
  title: string;
  author: string;
  edition_label: string;
}

export function LibraryPage() {
  const cache = useQueryClient();
  const [sort, setSort] = useState('newest');
  const [folder, setFolder] = useState('');
  const [favorite, setFavorite] = useState(false);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const params = new URLSearchParams({
    sort,
    offset: String(offset),
    limit: '20',
  });
  if (folder) params.set('folder_id', folder);
  if (favorite) params.set('favorite', 'true');
  const library = useQuery({
    queryKey: ['library', sort, folder, favorite, offset],
    queryFn: () =>
      request<{ items: Item[]; total: number }>(`/library?${params}`),
  });
  const folders = useQuery({
    queryKey: ['folders'],
    queryFn: () => request<{ items: Folder[] }>('/folders'),
  });
  const publicBooks = useQuery({
    queryKey: ['public-books'],
    queryFn: () => request<{ items: PublicBook[] }>('/books/public'),
  });

  async function change(action: () => Promise<unknown>, message = t.saved) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
      await Promise.all([
        cache.invalidateQueries({ queryKey: ['library'] }),
        cache.invalidateQueries({ queryKey: ['folders'] }),
      ]);
      setNotice(message);
      return true;
    } catch (failure) {
      setError(errorMessage(failure));
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    if (
      await change(() => request('/books/upload', 'POST', data), t.imported)
    ) {
      form.reset();
      setOffset(0);
    }
  }
  async function createFolder(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    if (
      await change(() =>
        request('/folders', 'POST', { name: new FormData(form).get('name') }),
      )
    )
      form.reset();
  }
  return (
    <section className="library-page">
      <h1>{t.title}</h1>
      <p>{t.intro}</p>
      <BookSearch />
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <details className="form-card">
        <summary>{t.upload}</summary>
        <p>{t.privateNotice}</p>
        <form onSubmit={(event) => void upload(event)}>
          <label>
            {t.file}
            <input
              name="file"
              type="file"
              accept=".txt,.pdf,.epub,.docx,.fb2"
              required
              disabled={busy}
            />
          </label>
          <label>
            {t.bookTitle}
            <input name="title" required maxLength={300} disabled={busy} />
          </label>
          <label>
            {t.author}
            <input
              name="author"
              defaultValue={t.unknownAuthor}
              required
              maxLength={300}
              disabled={busy}
            />
          </label>
          <label>
            {t.language}
            <input
              name="language"
              defaultValue="ru"
              required
              minLength={2}
              maxLength={32}
              disabled={busy}
            />
          </label>
          <label>
            {t.edition}
            <input
              name="edition_label"
              defaultValue={t.personalEdition}
              required
              maxLength={300}
              disabled={busy}
            />
          </label>
          <label>
            {t.translator}
            <input name="translator" maxLength={300} disabled={busy} />
          </label>
          <Button type="submit" disabled={busy}>
            {busy ? t.wait : t.import}
          </Button>
        </form>
      </details>
      <details className="form-card">
        <summary>{t.folders}</summary>
        <form onSubmit={(event) => void createFolder(event)}>
          <label>
            {t.folderName}
            <input name="name" required maxLength={100} disabled={busy} />
          </label>
          <Button type="submit" disabled={busy}>
            {t.createFolder}
          </Button>
        </form>
        {folders.isError && <p role="alert">{errorMessage(folders.error)}</p>}
        <ul>
          {folders.data?.items.map((f) => (
            <li key={f.id}>
              {f.name}{' '}
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  const name = new FormData(event.currentTarget).get('name');
                  void change(() =>
                    request(`/folders/${f.id}`, 'PATCH', { name }),
                  );
                }}
              >
                <label>
                  {t.renameFolder}
                  <input
                    name="name"
                    aria-label={`${t.renameFolder}: ${f.name}`}
                    defaultValue={f.name}
                    required
                    maxLength={100}
                    disabled={busy}
                  />
                </label>
                <Button type="submit" size="1" disabled={busy}>
                  {t.rename}
                </Button>
              </form>
              <Button
                size="1"
                variant="soft"
                disabled={busy}
                onClick={() =>
                  void change(async () => {
                    await request(`/folders/${f.id}`, 'DELETE');
                    if (folder === f.id) {
                      setFolder('');
                      setOffset(0);
                    }
                  })
                }
              >
                {t.deleteFolder}
              </Button>
            </li>
          ))}
        </ul>
        <p>{t.folderDeletionNotice}</p>
      </details>
      {!!publicBooks.data?.items.length && (
        <details className="form-card">
          <summary>{t.available}</summary>
          <ul>
            {publicBooks.data.items.map((book) => (
              <li key={book.text_version_id}>
                {book.title} · {book.author} · {book.edition_label}{' '}
                <Button
                  size="1"
                  disabled={busy}
                  onClick={() =>
                    void change(
                      () =>
                        request('/library', 'POST', {
                          text_version_id: book.text_version_id,
                        }),
                      t.imported,
                    )
                  }
                >
                  {t.add}
                </Button>
              </li>
            ))}
          </ul>
        </details>
      )}
      {publicBooks.isError && (
        <p role="alert">{errorMessage(publicBooks.error)}</p>
      )}
      <div className="library-filters">
        <label>
          {t.sort}
          <select
            aria-label={t.sort}
            value={sort}
            onChange={(event) => {
              setSort(event.target.value);
              setOffset(0);
            }}
          >
            <option value="newest">{t.newest}</option>
            <option value="oldest">{t.oldest}</option>
            <option value="title">{t.byTitle}</option>
            <option value="author">{t.byAuthor}</option>
          </select>
        </label>
        <label>
          {t.folder}
          <select
            aria-label={t.folderFilter}
            value={folder}
            onChange={(event) => {
              setFolder(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">{t.allFolders}</option>
            {folders.data?.items.map((f) => (
              <option value={f.id} key={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={favorite}
            onChange={(event) => {
              setFavorite(event.target.checked);
              setOffset(0);
            }}
          />
          {t.onlyFavorites}
        </label>
      </div>
      {library.isPending && <p role="status">{t.loading}</p>}
      {library.isError && (
        <p role="alert">
          {errorMessage(library.error)}{' '}
          <Button onClick={() => void library.refetch()}>{t.retry}</Button>
        </p>
      )}
      {library.data && (
        <p>
          {t.count}: {library.data.total}
        </p>
      )}
      {library.data?.total === 0 && <p>{t.empty}</p>}
      <ul className="book-list">
        {library.data?.items.map((item) => (
          <li className="book-card" key={item.id}>
            <h2>{item.title}</h2>
            <p>
              {item.author} · {item.edition_label}
              {item.translator ? ` · ${item.translator}` : ''} ·{' '}
              {item.format.toUpperCase()}
            </p>
            <div className="book-actions">
              <Link to={`/library/${item.id}/read`}>
                {messages.ru.reader.read}
              </Link>
              <Button
                variant="soft"
                aria-pressed={item.favorite}
                disabled={busy}
                onClick={() =>
                  void change(() =>
                    request(`/library/${item.id}`, 'PATCH', {
                      favorite: !item.favorite,
                    }),
                  )
                }
              >
                {item.favorite ? t.unfavorite : t.favorite}
              </Button>
              <label>
                {t.folder}
                <select
                  aria-label={`${t.folder}: ${item.title}`}
                  value={item.folder_id ?? ''}
                  disabled={busy}
                  onChange={(event) =>
                    void change(() =>
                      request(`/library/${item.id}`, 'PATCH', {
                        folder_id: event.target.value || null,
                      }),
                    )
                  }
                >
                  <option value="">{t.noFolder}</option>
                  {folders.data?.items.map((f) => (
                    <option value={f.id} key={f.id}>
                      {f.name}
                    </option>
                  ))}
                </select>
              </label>
              <a href={`/api/v1/library/${item.id}/original`} download>
                {t.download}
              </a>
              <details>
                <summary>{t.remove}</summary>
                <p>{t.deleteNotice}</p>
                <Button
                  color="red"
                  disabled={busy}
                  onClick={() =>
                    void change(
                      () => request(`/library/${item.id}`, 'DELETE'),
                      t.removed,
                    )
                  }
                >
                  {t.confirmRemove}
                </Button>
              </details>
            </div>
          </li>
        ))}
      </ul>
      {!!library.data?.total && (
        <nav aria-label={t.pagination}>
          <Button
            disabled={offset === 0 || busy}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            {t.previous}
          </Button>
          <Button
            disabled={offset + 20 >= library.data.total || busy}
            onClick={() => setOffset(offset + 20)}
          >
            {t.next}
          </Button>
        </nav>
      )}
    </section>
  );
}
