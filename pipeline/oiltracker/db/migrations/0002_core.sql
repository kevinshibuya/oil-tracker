set timezone = 'UTC';
create table ingest_manifest (
  port text not null, day date not null, source_url text, format text,
  bytes bigint, rows_kept bigint,
  status text not null check (status in ('running','done','failed','missing')), error text,
  started_at timestamptz not null default now(), finished_at timestamptz, loaded_at timestamptz,
  primary key (port, day));
create table vessels (
  mmsi integer primary key, imo integer, name text, call_sign text, vessel_type smallint,
  length_m real, width_m real, first_seen timestamptz not null, last_seen timestamptz not null,
  size_class text not null check (size_class in ('mr','panamax_lr1','aframax','suezmax','vlcc','unknown')),
  size_class_source text not null check (size_class_source in ('curated','estimated')),
  dwt_t real, tpc_t_per_cm real, scantling_draught_m real,
  particulars_source text check (particulars_source in ('curated','estimated')),
  code_version text not null, updated_at timestamptz not null default now());
create table vessel_statics_daily (
  port text not null, mmsi integer not null, day date not null,
  name text, imo integer, call_sign text, vessel_type smallint, length_m real, width_m real,
  cargo smallint, transceiver_class text,
  primary key (port, mmsi, day));
create table positions (
  port text not null, mmsi integer not null, ts timestamptz not null,
  geom geometry(Point,4326) not null, sog real, cog real, heading real,
  draught_m real, status smallint) partition by range (ts);
do $$ declare m date := '2023-01-01'; begin
  while m < '2025-10-01' loop
    execute format('create table positions_%s partition of positions for values from (%L) to (%L)',
      to_char(m,'YYYY_MM'), m, m + interval '1 month');
    m := m + interval '1 month'; end loop; end $$;
create index on positions (mmsi, ts);
create index on positions using gist (geom);
create table terminals (
  port text not null, terminal_id text not null, version integer not null, name text not null,
  crude text not null check (crude in ('yes','uncertain')), geom geometry(Polygon,4326) not null,
  primary key (port, terminal_id, version));
create table berth_visits (
  id bigserial primary key, port text not null, mmsi integer not null,
  terminal_id text not null, polygon_version integer not null,
  entered_at timestamptz not null, exited_at timestamptz not null,
  dwell_hours real not null, low_speed_fraction real not null, observations integer not null,
  open_ended boolean not null,
  code_version text not null, unique (port, mmsi, terminal_id, entered_at));
create table export_events (
  id bigserial primary key,
  berth_visit_id bigint not null unique references berth_visits(id) on delete cascade,
  port text not null, mmsi integer not null, imo integer, name text,
  size_class text not null, size_class_source text not null, length_m real, width_m real,
  dwt_t real, tpc_t_per_cm real, scantling_draught_m real, particulars_source text,
  terminal_id text not null, polygon_version integer not null,
  entered_at timestamptz not null, exited_at timestamptz not null,
  gate_crossed_at timestamptz not null, hours_to_gate real not null, gate_interpolated boolean not null,
  arrival_draught_m real, arrival_draught_at timestamptz,
  departure_draught_candidates jsonb not null,
  chosen_departure_draught_m real, draught_reason_codes text[],
  method text, consumables_t real, raw_tonnes real, load_factor real, k_scalar real, fit_window_id text,
  density_bbl_per_t real, barrels real, next_destination text, sts_flag boolean,
  code_version text not null, created_at timestamptz not null default now());
create table portwatch_daily (
  date date not null, portid text not null, portcalls integer, portcalls_tanker integer,
  export_tanker_t bigint, import_tanker_t bigint, fetched_at timestamptz not null,
  primary key (date, portid));
