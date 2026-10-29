"""Database tables. Everything lives in one DuckDB file so the whole state can be inspected with SQL."""
import duckdb

DDL = """
create table if not exists noi_actuals (          -- curated data the reports read (never edited in place)
    property_id varchar, month varchar, line_item varchar, amount decimal(14, 2),
    primary key (property_id, month, line_item));

create sequence if not exists override_seq start 1;
create table if not exists overrides (
    override_id integer primary key default nextval('override_seq'),
    property_id varchar, month varchar, line_item varchar,
    old_value decimal(14, 2), new_value decimal(14, 2),
    reason varchar, ticket varchar,
    status varchar,                                -- requested, approved, rejected, applied, reverted
    requested_by varchar, requested_at timestamp,
    approved_by varchar, approved_at timestamp,
    applied_by varchar, applied_at timestamp,
    validation varchar);

create sequence if not exists intake_seq start 1;
create table if not exists intake (
    intake_id integer primary key default nextval('intake_seq'),
    contract varchar, file_name varchar, sha256 varchar, month varchar, version integer,
    rows integer, amount_total decimal(16, 2),
    status varchar,                                -- rejected, validated, approved, loaded
    problems varchar,
    submitted_by varchar, submitted_at timestamp,
    approved_by varchar, approved_at timestamp,
    loaded_by varchar, loaded_at timestamp,
    reconciliation varchar);

create table if not exists capex_budget (
    intake_id integer, property_id varchar, cost_code varchar, amount decimal(14, 2), comment varchar);

create table if not exists audit_log (
    seq integer primary key, logged_at timestamp, actor varchar, action varchar, object varchar,
    details varchar, prev_hash varchar, hash varchar);

-- What the reports read: the base numbers with approved overrides applied on top.
create or replace view noi_actuals_final as
select a.property_id, a.month, a.line_item,
       coalesce(o.new_value, a.amount) as amount,
       o.override_id is not null      as overridden,
       o.override_id
from noi_actuals a
left join overrides o
  on o.status = 'applied'
 and (o.property_id, o.month, o.line_item) = (a.property_id, a.month, a.line_item);
"""


def connect(path):
    con = duckdb.connect(str(path))
    con.execute(DDL)
    return con
