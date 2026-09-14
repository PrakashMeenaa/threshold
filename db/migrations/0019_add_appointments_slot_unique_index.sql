BEGIN;

CREATE UNIQUE INDEX appointments_one_active_booking_per_slot
    ON appointments (slot_id) WHERE status = 'booked';

COMMIT;
