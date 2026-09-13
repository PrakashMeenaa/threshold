BEGIN;

ALTER TABLE departments ADD UNIQUE (clinic_id, id);
ALTER TABLE doctors ADD UNIQUE (clinic_id, id);
ALTER TABLE patients ADD UNIQUE (clinic_id, id);
ALTER TABLE availability_slots ADD UNIQUE (clinic_id, id);

ALTER TABLE doctors DROP CONSTRAINT doctors_department_id_fkey;
ALTER TABLE doctors ADD CONSTRAINT doctors_department_id_clinic_id_fkey FOREIGN KEY (department_id, clinic_id) REFERENCES departments (id, clinic_id);

ALTER TABLE availability_slots DROP CONSTRAINT availability_slots_doctor_id_fkey;
ALTER TABLE availability_slots ADD CONSTRAINT availability_slots_doctor_id_clinic_id_fkey FOREIGN KEY (doctor_id, clinic_id) REFERENCES doctors (id, clinic_id);

ALTER TABLE appointments DROP CONSTRAINT appointments_patient_id_fkey;
ALTER TABLE appointments ADD CONSTRAINT appointments_patient_id_clinic_id_fkey FOREIGN KEY (patient_id, clinic_id) REFERENCES patients (id, clinic_id);

ALTER TABLE appointments DROP CONSTRAINT appointments_doctor_id_fkey;
ALTER TABLE appointments ADD CONSTRAINT appointments_doctor_id_clinic_id_fkey FOREIGN KEY (doctor_id, clinic_id) REFERENCES doctors (id, clinic_id);

ALTER TABLE appointments DROP CONSTRAINT appointments_slot_id_fkey;
ALTER TABLE appointments ADD CONSTRAINT appointments_slot_id_clinic_id_fkey FOREIGN KEY (slot_id, clinic_id) REFERENCES availability_slots (id, clinic_id);

ALTER TABLE consents DROP CONSTRAINT consents_patient_id_fkey;
ALTER TABLE consents ADD CONSTRAINT consents_patient_id_clinic_id_fkey FOREIGN KEY (patient_id, clinic_id) REFERENCES patients (id, clinic_id);

ALTER TABLE conversation_state DROP CONSTRAINT conversation_state_patient_id_fkey;
ALTER TABLE conversation_state ADD CONSTRAINT conversation_state_patient_id_clinic_id_fkey FOREIGN KEY (patient_id, clinic_id) REFERENCES patients (id, clinic_id);

COMMIT;
