import { cookies } from "next/headers";
import { redirect } from "next/navigation";

export interface Session {
  staffUserId: string;
  clinicId: string;
  email: string;
  role: string;
  issuedAt: number;
  expiresAt: number;
}

export const SESSION_COOKIE_NAME = "threshold_session";
export const SESSION_DURATION_SECONDS = 8 * 60 * 60;

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} must be set`);
  }
  return value;
}

function bytesToHex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function hexToBytes(hex: string): Uint8Array<ArrayBuffer> {
  const bytes = new Uint8Array(new ArrayBuffer(hex.length / 2));
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

async function getKey(): Promise<CryptoKey> {
  const secret = requireEnv("SESSION_SECRET");
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

async function sign(payload: string): Promise<string> {
  const key = await getKey();
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return bytesToHex(signature);
}

export async function encodeSession(session: Session): Promise<string> {
  const payload = Buffer.from(JSON.stringify(session), "utf8").toString("base64url");
  const signature = await sign(payload);
  return `${payload}.${signature}`;
}

export async function decodeSession(cookieValue: string): Promise<Session | null> {
  const [payload, signature] = cookieValue.split(".");
  if (!payload || !signature) {
    return null;
  }

  const key = await getKey();
  const valid = await crypto.subtle.verify(
    "HMAC",
    key,
    hexToBytes(signature),
    new TextEncoder().encode(payload),
  );
  if (!valid) {
    return null;
  }

  const session = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as Session;
  if (session.expiresAt < Date.now()) {
    return null;
  }
  return session;
}

export async function getSession(): Promise<Session | null> {
  const cookieStore = await cookies();
  const raw = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  if (!raw) {
    return null;
  }
  return decodeSession(raw);
}

export async function requireSession(): Promise<Session> {
  const session = await getSession();
  if (session === null) {
    redirect("/login");
  }
  return session;
}

export async function setSessionCookie(session: Session): Promise<void> {
  const cookieStore = await cookies();
  cookieStore.set(SESSION_COOKIE_NAME, await encodeSession(session), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_DURATION_SECONDS,
  });
}

export async function clearSessionCookie(): Promise<void> {
  const cookieStore = await cookies();
  cookieStore.delete(SESSION_COOKIE_NAME);
}
