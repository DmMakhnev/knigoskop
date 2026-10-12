import { messages as translations } from '../app/messages';
import { Link, Route, Routes, useNavigate } from 'react-router-dom';
import { Protected } from '../components/Protected';
import { ReaderPage } from '../pages/ReaderPage';
import { LibraryPage } from '../pages/LibraryPage';
import { HomePage } from '../pages/HomePage';
import {
  LoginPage,
  RegisterPage,
  EmailRequestPage,
  TokenPage,
} from '../pages/AuthPages';
import { ProfilePage } from '../pages/ProfilePage';
import { AdminPage } from '../pages/AdminPage';
import { useSession } from '../features/auth/session';

const t = translations.ru.auth;

export function App() {
  const session = useSession();
  const navigate = useNavigate();
  return (
    <main>
      <nav aria-label={t.mainNavigation} className="main-nav">
        <Link to="/">Книгоскоп</Link>
        {session.data ? (
          <>
            <Link to="/library">{translations.ru.library.title}</Link>
            <Link to="/profile">{t.profile}</Link>
          </>
        ) : (
          <Link to="/login">{t.signIn}</Link>
        )}
        {session.data?.role === 'admin' && (
          <Link to="/admin">{t.administration}</Link>
        )}
      </nav>
      <Routes>
        <Route
          path="/"
          element={session.data ? <LibraryPage /> : <HomePage />}
        />
        <Route
          path="/library"
          element={
            <Protected next="/library">
              <LibraryPage />
            </Protected>
          }
        />
        <Route
          path="/library/:itemId/read"
          element={
            <Protected next="/library">
              <ReaderPage />
            </Protected>
          }
        />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/request-password-reset" element={<EmailRequestPage />} />
        <Route
          path="/request-verification"
          element={<EmailRequestPage verification />}
        />
        <Route path="/verify-email" element={<TokenPage key="verify" />} />
        <Route
          path="/reset-password"
          element={<TokenPage key="reset" reset />}
        />
        <Route
          path="/profile"
          element={
            <Protected>
              <ProfilePage
                onAccountDeleted={() => navigate('/')}
                onLoggedOut={() => navigate('/login')}
              />
            </Protected>
          }
        />
        <Route
          path="/admin"
          element={
            <Protected admin>
              <AdminPage />
            </Protected>
          }
        />
        <Route
          path="*"
          element={
            <section>
              <h1>{t.pageMissing}</h1>
              <Link to="/">{t.home}</Link>
            </section>
          }
        />
      </Routes>
    </main>
  );
}
