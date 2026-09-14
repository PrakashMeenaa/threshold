import type { PoolClient } from "pg";
import { withTenantConnection } from "@/lib/db";
import { requireSession } from "@/lib/session";

export interface AppointmentRow {
  id: string;
  patientName: string;
  doctorName: string;
  startsAtDisplay: string;
  status: string;
}

interface AppointmentQueryRow {
  id: string;
  patient_name: string | null;
  doctor_name: string;
  starts_at: Date;
  status: string;
}

function formatInTimezone(date: Date, tzName: string): string {
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: tzName,
  }).format(date);
}

function toAppointmentRow(row: AppointmentQueryRow, tzName: string): AppointmentRow {
  return {
    id: row.id,
    patientName: row.patient_name ?? "(name pending)",
    doctorName: row.doctor_name,
    startsAtDisplay: formatInTimezone(row.starts_at, tzName),
    status: row.status,
  };
}

async function fetchClinicTimezone(client: PoolClient, clinicId: string): Promise<string> {
  const result = await client.query<{ timezone: string }>(
    "SELECT timezone FROM clinics WHERE id = $1",
    [clinicId],
  );
  return result.rows[0]?.timezone ?? "UTC";
}

export async function fetchAppointmentById(
  clinicId: string,
  appointmentId: string,
): Promise<AppointmentRow | null> {
  return withTenantConnection(clinicId, async (client) => {
    const tzName = await fetchClinicTimezone(client, clinicId);
    const result = await client.query<AppointmentQueryRow>(
      `SELECT a.id, p.full_name AS patient_name, d.name AS doctor_name, s.starts_at, a.status
       FROM appointments a
       JOIN patients p ON p.id = a.patient_id
       JOIN doctors d ON d.id = a.doctor_id
       JOIN availability_slots s ON s.id = a.slot_id
       WHERE a.id = $1 AND a.clinic_id = $2`,
      [appointmentId, clinicId],
    );
    const row = result.rows[0];
    return row ? toAppointmentRow(row, tzName) : null;
  });
}

export async function fetchAppointments(clinicId: string): Promise<AppointmentRow[]> {
  return withTenantConnection(clinicId, async (client) => {
    const tzName = await fetchClinicTimezone(client, clinicId);
    const result = await client.query<AppointmentQueryRow>(
      `SELECT a.id, p.full_name AS patient_name, d.name AS doctor_name, s.starts_at, a.status
       FROM appointments a
       JOIN patients p ON p.id = a.patient_id
       JOIN doctors d ON d.id = a.doctor_id
       JOIN availability_slots s ON s.id = a.slot_id
       WHERE a.clinic_id = $1
       ORDER BY s.starts_at DESC`,
      [clinicId],
    );
    return result.rows.map((row) => toAppointmentRow(row, tzName));
  });
}

export default async function AppointmentsPage() {
  const session = await requireSession();
  const appointments = await fetchAppointments(session.clinicId);

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">Appointments</h1>
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-zinc-200 dark:border-zinc-800">
            <th className="py-2">Patient</th>
            <th className="py-2">Doctor</th>
            <th className="py-2">When</th>
            <th className="py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {appointments.map((appointment) => (
            <tr key={appointment.id} className="border-b border-zinc-100 dark:border-zinc-900">
              <td className="py-2">{appointment.patientName}</td>
              <td className="py-2">{appointment.doctorName}</td>
              <td className="py-2">{appointment.startsAtDisplay}</td>
              <td className="py-2">{appointment.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
