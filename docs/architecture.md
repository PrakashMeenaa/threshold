# Architecture

## RLS on `clinics`

`clinics` carries a self-referential RLS policy (`id = current_setting('app.current_clinic_id', true)::uuid`) rather than being left outside RLS, so that no table in the schema is exempt from tenant scoping at the database layer. The one exception is a single audited path: `resolve_clinic_by_phone(phone_number_id text) RETURNS uuid`, which lets the webhook handler resolve which clinic a message belongs to before any `app.current_clinic_id` session context exists. It returns only `clinic_id`, never the full row.

The function is `SECURITY DEFINER`, owned by a dedicated role, `threshold_rls_bypass` (`NOLOGIN`, `NOSUPERUSER`, `BYPASSRLS`) — not by the bootstrap role. `threshold_rls_bypass` exists solely to own this function; nothing ever connects as it. It holds a column-scoped grant, `SELECT (id, whatsapp_phone_number_id) ON clinics`, not the whole row, and `EXECUTE` on the function is revoked from `PUBLIC` and granted only to `threshold_api_user`. `threshold_app` additionally holds membership in `threshold_rls_bypass` (not login rights — just the membership Postgres requires to run `ALTER FUNCTION ... OWNER TO`).

This design is deliberate: it would be easy to let the function's `SECURITY DEFINER` bypass ride on `threshold_app` being a Postgres superuser, since that's true by default in local Docker (`initdb`'s user is always superuser, and superusers bypass RLS unconditionally, independent of `FORCE ROW LEVEL SECURITY`). But that's an accident of the dev environment, not a designed exception — no managed Postgres provider (RDS, Cloud SQL, Supabase) hands out true superuser to its admin role. Had the function stayed owned by `threshold_app`, it would silently start returning nothing for every phone number the moment `threshold_app` lost superuser status, breaking webhook routing for every clinic with nothing pointing at RLS as the cause. Routing the bypass through `threshold_rls_bypass` instead makes it an explicit, minimally-scoped, auditable grant that behaves identically regardless of `threshold_app`'s privilege level.

Example call: `SELECT resolve_clinic_by_phone($1) AS clinic_id;`

Because the `clinics` policy has no `FOR` clause, it also governs `INSERT`, and `WITH CHECK` is evaluated after column defaults are computed — so relying on `clinics.id`'s `gen_random_uuid()` default can never satisfy the check (the server-generated value can't be known in advance to match a session variable set beforehand). The default stays in the schema, but every real insert path must explicitly supply `id`, matching the session var set just before. In practice, creating a new clinic requires:

1. Generate the clinic's UUID application-side (not the column default).
2. `SET LOCAL app.current_clinic_id` to that same UUID.
3. Insert the `clinics` row, explicitly supplying `id`.
4. Only then proceed to insert `departments`, `doctors`, etc. for that clinic, as before.

This ordering is a hard requirement for the Step 1.3 seed script, not just for `clinics` — every dependent insert for a new clinic must happen after steps 1–3.
