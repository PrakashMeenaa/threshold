BEGIN;

ALTER TABLE patients ADD COLUMN department_id UUID;
ALTER TABLE patients ADD CONSTRAINT patients_department_id_clinic_id_fkey
    FOREIGN KEY (department_id, clinic_id) REFERENCES departments (id, clinic_id);

COMMIT;
