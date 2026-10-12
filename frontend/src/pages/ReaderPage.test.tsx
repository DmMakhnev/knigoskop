import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { ReaderPage } from './ReaderPage';
import { paragraphs, splitHighlight } from '../features/reader/text';
const text = 'А😀Б\nГлава 1\nТекст <script>bad()</script>';
const length = Array.from(text).length;
const leaf = {
  id: 'leaf',
  text_version_id: 'v1',
  parent_id: 'root',
  kind: 'fragment',
  title: 'Фрагмент',
  order_index: 1,
  start_offset: 0,
  end_offset: length,
};
const data = {
  item: { title: 'Книга', author: 'Автор', edition_label: 'Редакция' },
  text_version_id: 'v1',
  first_section_id: 'leaf',
  length,
  sections: [leaf],
  total: 1,
  progress: {
    revision: 4,
    position: {
      text_version_id: 'v1',
      section_id: 'leaf',
      offset: 1,
      end_offset: 2,
    },
  },
};
function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}
function api(conflict = false) {
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/csrf')) {
      document.cookie = 'knigoskop_csrf=x; path=/';
      return response({});
    }
    if (url.includes('/reading-settings'))
      return response({
        theme: 'dark',
        font: 'mono',
        font_size: 24,
        line_height: 2,
        width: 70,
      });
    if (url.includes('/reading-progress') && init?.method === 'PUT')
      return conflict
        ? response(
            {
              code: 'progress_conflict',
              message: 'Позиция изменена на другом устройстве',
            },
            409,
          )
        : response({ revision: 5 });
    if (url.includes('/processing-config'))
      return response({
        ready: false,
        external: false,
        models: [],
        latest_job_id: null,
      });
    if (url.includes('/summaries')) return response({ items: [], total: 0 });
    if (url.includes('/bookmarks')) return response({ items: [], total: 0 });
    if (url.includes('/texts/'))
      return response({
        text_version_id: 'v1',
        section_id: 'leaf',
        fragment: leaf,
        text,
        start_offset: 0,
        end_offset: length,
        highlight: { start_offset: 1, end_offset: 2 },
        previous_section_id: null,
        next_section_id: 'next',
      });
    return response(data);
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}
function mount(path = '/library/one/read') {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/library/:itemId/read" element={<ReaderPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.cookie = 'knigoskop_csrf=; Max-Age=0; path=/';
});

test('unicode offsets and highlight preserve astral symbols and newlines', () => {
  expect(paragraphs('А😀Б\nВ', 10)).toEqual([
    { text: 'А😀Б', start: 10, end: 13 },
    { text: 'В', start: 14, end: 15 },
  ]);
  expect(splitHighlight('А😀Б', 0, { start_offset: 1, end_offset: 2 })).toEqual(
    { before: 'А', selected: '😀', after: 'Б' },
  );
});

test('restores precise range and preferences; saves position with revision and escapes text', async () => {
  const mock = api();
  mount();
  await screen.findByRole('heading', { name: 'Книга' });
  await waitFor(() =>
    expect(document.querySelector('mark')?.textContent).toBe('😀'),
  );
  expect(document.querySelector('.reader-text script')).toBeNull();
  await waitFor(() =>
    expect(document.querySelector('.reader-dark')).toBeTruthy(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить позицию' }));
  await screen.findByText('Позиция чтения сохранена');
  const body = JSON.parse(
    mock.mock.calls.find(([url]) => url.endsWith('/reading-progress'))![1]!
      .body as string,
  );
  expect(body).toMatchObject({
    library_item_id: 'one',
    text_version_id: 'v1',
    section_id: 'leaf',
    offset: 1,
    end_offset: 2,
    revision: 4,
  });
});

test('foreign version link never requests original text', async () => {
  const mock = api();
  mount('/library/one/read?version=another&section=leaf&start=1&end=2');
  await screen.findByRole('alert');
  expect(mock.mock.calls.some(([url]) => url.includes('/texts/'))).toBe(false);
  expect(document.querySelector('.reader-text')).toBeNull();
});

test('conflict preserves remote progress and requires refresh instead of overwriting', async () => {
  const mock = api(true);
  mount();
  await screen.findByRole('button', { name: 'Сохранить позицию' });
  await waitFor(() => expect(document.querySelector('mark')).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить позицию' }));
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить позицию' }));
  expect(
    mock.mock.calls.filter(([url]) => url.endsWith('/reading-progress')),
  ).toHaveLength(1);
});

test('bookmark retains exact selected range and summary is empty before processing', async () => {
  const mock = api();
  mount();
  await screen.findByRole('button', { name: 'Добавить закладку' });
  fireEvent.change(screen.getByLabelText('Название закладки'), {
    target: { value: 'Символ' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Добавить закладку' }));
  await screen.findByText('Закладка сохранена');
  const call = mock.mock.calls.find(
    ([url, init]) => url.includes('/bookmarks') && init?.method === 'POST',
  );
  expect(JSON.parse(call![1]!.body as string)).toMatchObject({
    text_version_id: 'v1',
    section_id: 'leaf',
    start_offset: 1,
    end_offset: 2,
    label: 'Символ',
  });
  fireEvent.click(screen.getByRole('tab', { name: 'Резюме' }));
  expect(screen.getByText('Резюме пока не создано.')).toBeTruthy();
});
