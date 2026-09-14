import { withTenantConnection } from "@/lib/db";
import { requireSession } from "@/lib/session";

interface PatientQueryRow {
  id: string;
  full_name: string | null;
  preferred_language: string | null;
  department_name: string | null;
  consent_granted: boolean | null;
}

export interface PatientRow {
  id: string;
  displayName: string;
  preferredLanguage: string | null;
  departmentName: string | null;
  consentStatus: "granted" | "declined" | "pending";
}

export function toPatientRow(row: PatientQueryRow): PatientRow {
  if (row.consent_granted === false) {
    return {
      id: row.id,
      displayName: "Declined contact",
      preferredLanguage: null,
      departmentName: null,
      consentStatus: "declined",
    };
  }
  return {
    id: row.id,
    displayName: row.full_name ?? "(name pending)",
    preferredLanguage: row.preferred_language,
    departmentName: row.department_name,
    consentStatus: row.consent_granted === true ? "granted" : "pending",
  };
}

export async function fetchPatientById(
  clinicId: string,
  patientId: string,
): Promise<PatientRow | null> {
  return withTenantConnection(clinicId, async (client) => {
    const result = await client.query<PatientQueryRow>(
      `SELECT p.id, p.full_name, p.preferred_language, d.name AS department_name,
              (
                SELECT c.granted FROM consents c
                WHERE c.patient_id = p.id
                ORDER BY c.granted_at DESC
                LIMIT 1
              ) AS consent_granted
       FROM patients p
       LEFT JOIN departments d ON d.id = p.department_id
       WHERE p.id = $1 AND p.clinic_id = $2`,
      [patientId, clinicId],
    );
    const row = result.rows[0];
    return row ? toPatientRow(row) : null;
  });
}

export async function fetchPatients(clinicId: string): Promise<PatientRow[]> {
  return withTenantConnection(clinicId, async (client) => {
    const result = await client.query<PatientQueryRow>(
      `SELECT p.id, p.full_name, p.preferred_language, d.name AS department_name,
              (
                SELECT c.granted FROM consents c
                WHERE c.patient_id = p.id
                ORDER BY c.granted_at DESC
                LIMIT 1
              ) AS consent_granted
       FROM patients p
       LEFT JOIN departments d ON d.id = p.department_id
       WHERE p.clinic_id = $1
       ORDER BY p.created_at DESC`,
      [clinicId],
    );
    return result.rows.map(toPatientRow);
  });
}

const CONSENT_LABEL: Record<PatientRow["consentStatus"], string> = {
  granted: "Consented",
  declined: "Declined",
  pending: "Pending",
};

export default async function PatientsPage() {
  const session = await requireSession();
  const patients = await fetchPatients(session.clinicId);

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">Patients</h1>
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-zinc-200 dark:border-zinc-800">
            <th className="py-2">Name</th>
            <th className="py-2">Language</th>
            <th className="py-2">Department</th>
            <th className="py-2">Consent</th>
          </tr>
        </thead>
        <tbody>
          {patients.map((patient) => (
            <tr key={patient.id} className="border-b border-zinc-100 dark:border-zinc-900">
              <td className="py-2">{patient.displayName}</td>
              <td className="py-2">{patient.preferredLanguage ?? "—"}</td>
              <td className="py-2">{patient.departmentName ?? "—"}</td>
              <td className="py-2">{CONSENT_LABEL[patient.consentStatus]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
