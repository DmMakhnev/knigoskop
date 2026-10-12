export function App() {
  return (
    <main>
      <p className="eyebrow">Смотри в самую суть книги</p>
      <h1>Книгоскоп</h1>
      <p>Поиск книг, чтение оригинала, многоуровневые резюме и озвучивание.</p>
      <section aria-labelledby="status-title">
        <h2 id="status-title">Начало разработки</h2>
        <p>Каркас приложения готов. Библиотека, авторизация, AI и TTS появятся на следующих этапах.</p>
        <a href="/api/v1/status">Диагностика API</a>
      </section>
    </main>
  );
}
