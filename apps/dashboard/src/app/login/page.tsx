"use client";

import { useActionState } from "react";
import { loginAction, type LoginActionState } from "./actions";

const initialState: LoginActionState = { error: null };

export default function LoginPage() {
  const [state, formAction, pending] = useActionState(loginAction, initialState);

  return (
    <main className="flex min-h-screen items-center justify-center bg-zinc-50 dark:bg-black">
      <form
        action={formAction}
        className="flex w-full max-w-sm flex-col gap-4 rounded-lg border border-zinc-200 p-8 dark:border-zinc-800"
      >
        <h1 className="text-lg font-semibold">Clinic staff login</h1>
        <label className="flex flex-col gap-1 text-sm">
          Email
          <input
            type="email"
            name="email"
            required
            autoComplete="email"
            className="rounded border border-zinc-300 px-3 py-2 dark:border-zinc-700"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Password
          <input
            type="password"
            name="password"
            required
            autoComplete="current-password"
            className="rounded border border-zinc-300 px-3 py-2 dark:border-zinc-700"
          />
        </label>
        {state.error !== null ? (
          <p className="text-sm text-red-600" role="alert">
            {state.error}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={pending}
          className="rounded bg-zinc-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
        >
          {pending ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </main>
  );
}
