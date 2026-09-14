import { beforeAll, describe, expect, it } from "vitest";
import { pool } from "@/lib/db";
import { verifyStaffCredentials } from "@/lib/auth";
import { fetchPatientById, fetchPatients } from "@/app/dashboard/patients/page";
import { fetchAppointmentById } from "@/app/dashboard/appointments/page";

let sunriseClinicId: string;
let cityDentalClinicId: string;
let sunrisePatientId: string;
let sunriseAppointmentId: string;

beforeAll(async () => {
  const client = await pool.connect();
  try {
    sunriseClinicId = (
      await client.query<{ id: string }>("SELECT resolve_clinic_by_phone($1) AS id", [
        "sunrise-main",
      ])
    ).rows[0].id;
    cityDentalClinicId = (
      await client.query<{ id: string }>("SELECT resolve_clinic_by_phone($1) AS id", [
        "city-dental-main",
      ])
    ).rows[0].id;

    await client.query("SELECT set_config('app.current_clinic_id', $1, false)", [
      sunriseClinicId,
    ]);

    sunrisePatientId = (
      await client.query<{ id: string }>(
        "INSERT INTO patients (clinic_id, whatsapp_id, full_name) VALUES ($1, $2, $3) RETURNING id",
        [sunriseClinicId, `+1${Math.floor(Math.random() * 1e10)}`, "Vitest Test Patient"],
      )
    ).rows[0].id;

    const doctorId = (
      await client.query<{ id: string }>("SELECT id FROM doctors WHERE clinic_id = $1 LIMIT 1", [
        sunriseClinicId,
      ])
    ).rows[0].id;

    const slotId = (
      await client.query<{ id: string }>(
        `INSERT INTO availability_slots (clinic_id, doctor_id, starts_at, ends_at, status)
         VALUES ($1, $2, now() + interval '120 days', now() + interval '120 days 30 minutes', 'booked')
         RETURNING id`,
        [sunriseClinicId, doctorId],
      )
    ).rows[0].id;

    sunriseAppointmentId = (
      await client.query<{ id: string }>(
        `INSERT INTO appointments (clinic_id, patient_id, doctor_id, slot_id, status)
         VALUES ($1, $2, $3, $4, 'booked')
         RETURNING id`,
        [sunriseClinicId, sunrisePatientId, doctorId, slotId],
      )
    ).rows[0].id;
  } finally {
    client.release();
  }
});

describe("staff login", () => {
  it("resolves the correct clinic for a valid email/password", async () => {
    const identity = await verifyStaffCredentials(
      "staff@sunrise-multi-speciality.example",
      process.env.STAFF_SEED_PASSWORD ?? "",
    );
    expect(identity).not.toBeNull();
    expect(identity?.clinicId).toBe(sunriseClinicId);
  });

  it("is case-insensitive and whitespace-tolerant on email", async () => {
    const identity = await verifyStaffCredentials(
      "  Staff@Sunrise-Multi-Speciality.EXAMPLE  ",
      process.env.STAFF_SEED_PASSWORD ?? "",
    );
    expect(identity).not.toBeNull();
    expect(identity?.clinicId).toBe(sunriseClinicId);
  });

  it("rejects a wrong password", async () => {
    const identity = await verifyStaffCredentials(
      "staff@sunrise-multi-speciality.example",
      "definitely-wrong-password",
    );
    expect(identity).toBeNull();
  });

  it("rejects a nonexistent email", async () => {
    const identity = await verifyStaffCredentials("nobody@example.com", "whatever");
    expect(identity).toBeNull();
  });
});

describe("tenant isolation", () => {
  it("only returns patients belonging to the queried clinic", async () => {
    const sunrisePatients = await fetchPatients(sunriseClinicId);
    expect(sunrisePatients.some((patient) => patient.id === sunrisePatientId)).toBe(true);

    const cityDentalPatients = await fetchPatients(cityDentalClinicId);
    expect(cityDentalPatients.some((patient) => patient.id === sunrisePatientId)).toBe(false);
  });

  it("rejects fetching a patient by ID when scoped to the wrong clinic (IDOR)", async () => {
    expect(await fetchPatientById(cityDentalClinicId, sunrisePatientId)).toBeNull();
    expect(await fetchPatientById(sunriseClinicId, sunrisePatientId)).not.toBeNull();
  });

  it("rejects fetching an appointment by ID when scoped to the wrong clinic (IDOR)", async () => {
    expect(await fetchAppointmentById(cityDentalClinicId, sunriseAppointmentId)).toBeNull();
    expect(await fetchAppointmentById(sunriseClinicId, sunriseAppointmentId)).not.toBeNull();
  });
});
