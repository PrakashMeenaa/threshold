import { describe, expect, it } from "vitest";
import { toPatientRow } from "@/app/dashboard/patients/page";

describe("toPatientRow", () => {
  it("redacts identifying details when consent was declined", () => {
    const row = toPatientRow({
      id: "p1",
      full_name: "Real Name",
      preferred_language: "en",
      department_name: "Cardiology",
      consent_granted: false,
    });
    expect(row.displayName).toBe("Declined contact");
    expect(row.preferredLanguage).toBeNull();
    expect(row.departmentName).toBeNull();
    expect(row.consentStatus).toBe("declined");
  });

  it("shows full details when consent was granted", () => {
    const row = toPatientRow({
      id: "p1",
      full_name: "Real Name",
      preferred_language: "en",
      department_name: "Cardiology",
      consent_granted: true,
    });
    expect(row.displayName).toBe("Real Name");
    expect(row.preferredLanguage).toBe("en");
    expect(row.consentStatus).toBe("granted");
  });

  it("treats no consent decision yet as pending, not declined", () => {
    const row = toPatientRow({
      id: "p1",
      full_name: "Real Name",
      preferred_language: null,
      department_name: null,
      consent_granted: null,
    });
    expect(row.displayName).toBe("Real Name");
    expect(row.consentStatus).toBe("pending");
  });
});
