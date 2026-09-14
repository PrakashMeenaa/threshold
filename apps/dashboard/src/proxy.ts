import { NextResponse, type NextRequest } from "next/server";
import { decodeSession, SESSION_COOKIE_NAME } from "@/lib/session";

export async function proxy(request: NextRequest): Promise<NextResponse> {
  const cookieValue = request.cookies.get(SESSION_COOKIE_NAME)?.value;
  const session = cookieValue ? await decodeSession(cookieValue) : null;

  if (session === null) {
    if (request.nextUrl.pathname.startsWith("/api/")) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/api/:path*"],
};
