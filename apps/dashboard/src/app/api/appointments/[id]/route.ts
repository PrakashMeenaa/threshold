import { NextResponse } from "next/server";
import { getSession } from "@/lib/session";
import { fetchAppointmentById } from "@/app/dashboard/appointments/page";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const session = await getSession();
  if (session === null) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const appointment = await fetchAppointmentById(session.clinicId, id);

  if (appointment === null) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  return NextResponse.json(appointment);
}
