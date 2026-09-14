import Link from "next/link";
import type { ReactNode } from "react";
import { requireSession } from "@/lib/session";
import { logoutAction } from "./actions";

export default async function DashboardLayout({ children }: { children: ReactNode }) {
  const session = await requireSession();

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <nav className="flex items-center justify-between border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
        <div className="flex gap-6 text-sm font-medium">
          <Link href="/dashboard/patients">Patients</Link>
          <Link href="/dashboard/appointments">Appointments</Link>
        </div>
        <div className="flex items-center gap-4 text-sm text-zinc-500">
          <span>{session.email}</span>
          <form action={logoutAction}>
            <button type="submit" className="underline">
              Log out
            </button>
          </form>
        </div>
      </nav>
      <main className="p-6">{children}</main>
    </div>
  );
}
