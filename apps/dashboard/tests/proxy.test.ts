import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { proxy } from "@/proxy";

describe("proxy", () => {
  it("redirects unauthenticated page requests to /login", async () => {
    const request = new NextRequest("http://localhost:3000/dashboard/patients");
    const response = await proxy(request);
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toContain("/login");
  });

  it("returns 401 JSON for unauthenticated API requests", async () => {
    const request = new NextRequest("http://localhost:3000/api/patients/abc");
    const response = await proxy(request);
    expect(response.status).toBe(401);
  });

  it("allows a request through when a valid session cookie is present", async () => {
    const { encodeSession, SESSION_COOKIE_NAME } = await import("@/lib/session");
    const cookieValue = await encodeSession({
      staffUserId: "11111111-1111-1111-1111-111111111111",
      clinicId: "22222222-2222-2222-2222-222222222222",
      email: "staff@example.com",
      role: "staff",
      issuedAt: Date.now(),
      expiresAt: Date.now() + 60_000,
    });
    const request = new NextRequest("http://localhost:3000/dashboard/patients", {
      headers: { cookie: `${SESSION_COOKIE_NAME}=${cookieValue}` },
    });
    const response = await proxy(request);
    expect(response.status).toBe(200);
  });
});
