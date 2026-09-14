import { headers } from "next/headers";

export async function verifySameOrigin(): Promise<void> {
  const headerList = await headers();
  const origin = headerList.get("origin");
  const host = headerList.get("host") ?? headerList.get("x-forwarded-host");

  if (!origin || !host) {
    throw new Error("Missing origin or host header");
  }

  if (new URL(origin).host !== host) {
    throw new Error("Cross-origin request rejected");
  }
}
