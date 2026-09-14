import { describe, expect, it } from "vitest";
import { decodeSession, encodeSession, type Session } from "@/lib/session";

const baseSession: Session = {
  staffUserId: "11111111-1111-1111-1111-111111111111",
  clinicId: "22222222-2222-2222-2222-222222222222",
  email: "staff@example.com",
  role: "staff",
  issuedAt: Date.now(),
  expiresAt: Date.now() + 60_000,
};

describe("session encoding", () => {
  it("round-trips a valid session", async () => {
    const encoded = await encodeSession(baseSession);
    const decoded = await decodeSession(encoded);
    expect(decoded).toEqual(baseSession);
  });

  it("rejects a tampered payload", async () => {
    const encoded = await encodeSession(baseSession);
    const [, signature] = encoded.split(".");
    const tamperedPayload = Buffer.from(
      JSON.stringify({ ...baseSession, role: "admin" }),
    ).toString("base64url");
    expect(await decodeSession(`${tamperedPayload}.${signature}`)).toBeNull();
  });

  it("rejects an expired session", async () => {
    const expired = { ...baseSession, expiresAt: Date.now() - 1000 };
    const encoded = await encodeSession(expired);
    expect(await decodeSession(encoded)).toBeNull();
  });

  it("rejects a malformed cookie value", async () => {
    expect(await decodeSession("not-a-valid-cookie")).toBeNull();
  });
});
