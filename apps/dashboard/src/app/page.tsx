import { redirect } from "next/navigation";
import { getSession } from "@/lib/session";

export default async function Home(): Promise<never> {
  const session = await getSession();
  redirect(session === null ? "/login" : "/dashboard/patients");
}
