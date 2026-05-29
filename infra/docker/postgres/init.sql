-- NeuroNOC Postgres initialization
-- Phase 1: schema intentionally empty. Real tables land in Phase 2 (incidents data model).

DO $$
BEGIN
    RAISE NOTICE 'NeuroNOC Postgres initialized (Phase 1 scaffold — no schema yet).';
END
$$;
