"use server";

import { redirect } from "next/navigation";
import { verifyStaffCredentials } from "@/lib/auth";
import { verifySameOrigin } from "@/lib/csrf";
import { SESSION_DURATION_SECONDS, setSessionCookie } from "@/lib/session";

export interface LoginActionState {
  error: string | null;
}

export async function loginAction(
  _prevState: LoginActionState,
  formData: FormData,
): Promise<LoginActionState> {
  await verifySameOrigin();

  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");

  if (!email || !password) {
    return { error: "Invalid email or password" };
  }

  const identity = await verifyStaffCredentials(email, password);
  if (identity === null) {
    return { error: "Invalid email or password" };
  }

  const now = Date.now();
  await setSessionCookie({
    staffUserId: identity.staffUserId,
    clinicId: identity.clinicId,
    email: identity.email,
    role: identity.role,
    issuedAt: now,
    expiresAt: now + SESSION_DURATION_SECONDS * 1000,
  });

  redirect("/dashboard/patients");
}
