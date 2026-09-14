import bcrypt from "bcryptjs";
import { pool } from "./db";

export interface StaffIdentity {
  staffUserId: string;
  clinicId: string;
  email: string;
  role: string;
}

interface StaffCandidateRow {
  id: string;
  clinic_id: string;
  password_hash: string;
  role: string;
}

const DUMMY_HASH = "$2b$10$CwTycUXWue0Thq9StjUM0uJ8h7l8h1U8dQ2lY0e3Gq2f0x1lJ8m9O";

export async function verifyStaffCredentials(
  email: string,
  password: string,
): Promise<StaffIdentity | null> {
  const normalizedEmail = email.trim().toLowerCase();

  const client = await pool.connect();
  let candidates: StaffCandidateRow[];
  try {
    const result = await client.query<StaffCandidateRow>(
      "SELECT id, clinic_id, password_hash, role FROM resolve_staff_by_email($1)",
      [normalizedEmail],
    );
    candidates = result.rows;
  } finally {
    client.release();
  }

  for (const candidate of candidates) {
    if (await bcrypt.compare(password, candidate.password_hash)) {
      return {
        staffUserId: candidate.id,
        clinicId: candidate.clinic_id,
        email: normalizedEmail,
        role: candidate.role,
      };
    }
  }

  await bcrypt.compare(password, DUMMY_HASH);
  return null;
}
