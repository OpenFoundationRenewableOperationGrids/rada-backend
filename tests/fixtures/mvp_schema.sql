-- tests/fixtures/mvp_schema.sql
--
-- The MVP schema as it exists on databases created before Alembic: a copy of
-- a develop database at bdf56ba (tables built by create_all() plus
-- migrations/001_assets_gps_edge.sql). Used by tests/test_migrations.py and CI
-- to prove that stamp 0001 + upgrade head works on a real MVP schema.
--
-- Made with the pg_dump of the pinned container (16.15, from
-- timescale/timescaledb:2.32.0-pg16), never a host pg_dump of another major
-- version, from rada_mvp_snapshot (an untouched copy of an MVP database), with
-- the \restrict / \unrestrict lines removed (psql-only, random key per dump):
--
--   docker exec rada-tsdb pg_dump -U rada -d rada_mvp_snapshot \
--     --schema-only --no-owner --no-privileges -n public \
--     | grep -v '^\\\(un\)\?restrict '
--
-- then this header put back on top.
-- Load it into an empty database after DROP SCHEMA public CASCADE (the dump
-- recreates the schema). Never regenerate it from a migrated database.

--
-- PostgreSQL database dump
--


-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: assetstatus; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.assetstatus AS ENUM (
    'COMMUNICATING',
    'UNREACHABLE'
);


--
-- Name: assettype; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.assettype AS ENUM (
    'BATTERY',
    'SOLAR',
    'WIND'
);


--
-- Name: gridconnectionstatus; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.gridconnectionstatus AS ENUM (
    'ACTIVE',
    'CURTAILED',
    'HOLDING',
    'FAULT'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assets (
    id integer NOT NULL,
    asset_type public.assettype NOT NULL,
    eic_code character varying(20),
    name character varying(100) NOT NULL,
    max_charge_rate_mw double precision NOT NULL,
    max_discharge_rate_mw double precision NOT NULL,
    reactive_power_capacity_mvar double precision,
    max_capacity_mwh double precision NOT NULL,
    efficiency double precision,
    latitude double precision,
    longitude double precision,
    edge_id character varying,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


--
-- Name: assets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.assets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: assets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.assets_id_seq OWNED BY public.assets.id;


--
-- Name: dispatch_commands; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dispatch_commands (
    id integer NOT NULL,
    "timestamp" timestamp without time zone,
    asset_id integer NOT NULL,
    command_type character varying(50) NOT NULL,
    power_target_mw double precision NOT NULL,
    duration_seconds integer NOT NULL,
    status character varying(20) NOT NULL
);


--
-- Name: dispatch_commands_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dispatch_commands_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dispatch_commands_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dispatch_commands_id_seq OWNED BY public.dispatch_commands.id;


--
-- Name: grid_signals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.grid_signals (
    id integer NOT NULL,
    "timestamp" timestamp without time zone,
    total_generation_mw double precision,
    renewable_mw double precision,
    renewable_pct double precision,
    imbalance_mw double precision,
    imbalance_trend character varying(20),
    fcr_activated_mw double precision,
    calculated_frequency_hz double precision,
    status character varying(50) NOT NULL
);


--
-- Name: grid_signals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.grid_signals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: grid_signals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.grid_signals_id_seq OWNED BY public.grid_signals.id;


--
-- Name: state_of_charge; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.state_of_charge (
    id integer NOT NULL,
    "timestamp" timestamp with time zone NOT NULL,
    asset_id integer NOT NULL,
    operational_mode public.gridconnectionstatus,
    asset_status public.assetstatus,
    energy_mwh double precision NOT NULL,
    voltage double precision,
    current_amps double precision,
    temperature_celsius double precision,
    power_mw double precision,
    reactive_power_mvar double precision,
    power_factor double precision
);


--
-- Name: state_of_charge_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.state_of_charge_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: state_of_charge_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.state_of_charge_id_seq OWNED BY public.state_of_charge.id;


--
-- Name: assets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets ALTER COLUMN id SET DEFAULT nextval('public.assets_id_seq'::regclass);


--
-- Name: dispatch_commands id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_commands ALTER COLUMN id SET DEFAULT nextval('public.dispatch_commands_id_seq'::regclass);


--
-- Name: grid_signals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.grid_signals ALTER COLUMN id SET DEFAULT nextval('public.grid_signals_id_seq'::regclass);


--
-- Name: state_of_charge id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.state_of_charge ALTER COLUMN id SET DEFAULT nextval('public.state_of_charge_id_seq'::regclass);


--
-- Name: assets assets_eic_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_eic_code_key UNIQUE (eic_code);


--
-- Name: assets assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_pkey PRIMARY KEY (id);


--
-- Name: dispatch_commands dispatch_commands_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_commands
    ADD CONSTRAINT dispatch_commands_pkey PRIMARY KEY (id);


--
-- Name: grid_signals grid_signals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.grid_signals
    ADD CONSTRAINT grid_signals_pkey PRIMARY KEY (id);


--
-- Name: state_of_charge state_of_charge_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.state_of_charge
    ADD CONSTRAINT state_of_charge_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: ix_assets_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_assets_id ON public.assets USING btree (id);


--
-- Name: ix_dispatch_commands_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispatch_commands_id ON public.dispatch_commands USING btree (id);


--
-- Name: ix_dispatch_commands_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispatch_commands_timestamp ON public.dispatch_commands USING btree ("timestamp");


--
-- Name: ix_grid_signals_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_grid_signals_id ON public.grid_signals USING btree (id);


--
-- Name: ix_grid_signals_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_grid_signals_timestamp ON public.grid_signals USING btree ("timestamp");


--
-- Name: ix_state_of_charge_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_state_of_charge_timestamp ON public.state_of_charge USING btree ("timestamp");


--
-- Name: dispatch_commands dispatch_commands_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_commands
    ADD CONSTRAINT dispatch_commands_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES public.assets(id);


--
-- Name: state_of_charge state_of_charge_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.state_of_charge
    ADD CONSTRAINT state_of_charge_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES public.assets(id);


--
-- PostgreSQL database dump complete
--


