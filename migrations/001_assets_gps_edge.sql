-- 001_assets_gps_edge.sql
--
-- Adds the GPS (latitude, longitude) and OpenEMS edge_id columns to the
-- assets table. Base.metadata.create_all() only creates missing tables and
-- never adds columns to existing ones, so this must be run on existing
-- databases before deploying this release. Idempotent: safe to run twice.

ALTER TABLE assets
  ADD COLUMN IF NOT EXISTS latitude  DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS edge_id   VARCHAR;
