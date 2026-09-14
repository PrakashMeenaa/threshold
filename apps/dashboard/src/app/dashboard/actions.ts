"use server";

import { redirect } from "next/navigation";
import { verifySameOrigin } from "@/lib/csrf";
import { clearSessionCookie } from "@/lib/session";

export async function logoutAction(): Promise<never> {
  await verifySameOrigin();
  await clearSessionCookie();
  redirect("/login");
}
