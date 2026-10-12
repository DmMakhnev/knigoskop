import { replaceSession } from '../features/auth/session';
import { messages as translations } from '../app/messages';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { AuthForm } from '../components/AuthForm';
import { request } from '../api/auth';
import type { User } from '../api/auth';

const t = translations.ru.auth;

interface Message {
  message: string;
}

export function LoginPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [search] = useSearchParams();
  return (
    <AuthForm
      title={t.login}
      button={t.signIn}
      password
      onSubmit={async (data) => {
        const user = await request<User>('/auth/login', 'POST', data);
        await replaceSession(queryClient, user);
        const next = search.get('next');
        navigate(
          next === '/admin' || next === '/profile' || next === '/library'
            ? next
            : '/profile',
          {
            replace: true,
          },
        );
      }}
    >
      <p>
        <Link to="/request-password-reset">{t.forgotPassword}</Link>
      </p>
      <p>
        <Link to="/request-verification">{t.resendVerification}</Link>
      </p>
      <p>
        <Link to="/register">{t.createAccount}</Link>
      </p>
    </AuthForm>
  );
}

export function RegisterPage() {
  return (
    <AuthForm
      title={t.registration}
      button={t.register}
      password
      newPassword
      onSubmit={async (data) => {
        return (await request<Message>('/auth/register', 'POST', data)).message;
      }}
    >
      <p>
        <Link to="/login">{t.alreadyRegistered}</Link>
      </p>
    </AuthForm>
  );
}

export function EmailRequestPage({
  verification = false,
}: {
  verification?: boolean;
}) {
  return (
    <AuthForm
      title={verification ? t.emailVerification : t.passwordRecovery}
      button={t.sendEmail}
      onSubmit={async ({ email }) => {
        return (
          await request<Message>(
            verification
              ? '/auth/request-verification'
              : '/auth/request-password-reset',
            'POST',
            { email },
          )
        ).message;
      }}
    >
      <p>
        <Link to="/login">{t.backToLogin}</Link>
      </p>
    </AuthForm>
  );
}

function takeFragmentToken(): string {
  const token =
    new URLSearchParams(window.location.hash.slice(1)).get('token') ?? '';
  // Prevent tokens being retained in browser history or sent as referrers.
  window.history.replaceState(
    window.history.state,
    '',
    window.location.pathname + window.location.search,
  );
  return token;
}
export function TokenPage({ reset = false }: { reset?: boolean }) {
  const queryClient = useQueryClient();
  // Lazy initialization must not consume the fragment twice under StrictMode.
  const [token] = useState(
    () => new URLSearchParams(window.location.hash.slice(1)).get('token') ?? '',
  );
  useEffect(() => {
    takeFragmentToken();
  }, []);
  if (!token)
    return (
      <section>
        <h1>{t.invalidLink}</h1>
        <p>{t.requestNewEmail}</p>
        <Link to={reset ? '/request-password-reset' : '/request-verification'}>
          {t.newLink}
        </Link>
      </section>
    );
  return (
    <AuthForm
      title={reset ? t.newPassword : t.emailVerification}
      button={reset ? t.changePassword : t.verifyEmail}
      email={false}
      password={reset}
      newPassword={reset}
      onSubmit={async ({ password }) => {
        takeFragmentToken();
        const result = await request<Message>(
          reset ? '/auth/reset-password' : '/auth/verify-email',
          'POST',
          reset ? { token, password } : { token },
        );
        if (reset) {
          await replaceSession(queryClient, null);
        }
        return result.message;
      }}
    >
      <p>
        <Link to="/login">{t.goToLogin}</Link>
      </p>
      <p>
        <Link to={reset ? '/request-password-reset' : '/request-verification'}>
          {t.newLink}
        </Link>
      </p>
    </AuthForm>
  );
}
