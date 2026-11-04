-- M10 baseline audit queries. Run: docker compose exec -T postgres psql -U commerceradar -f - < docs/m10-audit-queries.sql
\pset footer off
\echo ===== T1 BASELINE =====
select now() as measured_at;
select
  (select count(*) from discovered_urls)                                   as candidate_records_all_sources,
  (select count(*) from domains)                                           as domains_rows,
  (select count(distinct normalized_domain) from domains)                  as unique_normalized_domains,
  (select count(*) from domains where suppressed_at is not null)           as suppressed,
  (select count(*) from domains where verification_status <> 'pending')    as processed_at_least_once,
  (select count(*) from domains where verification_status = 'pending')     as never_processed,
  (select count(*) from domains where current_resolution_status = 'resolved') as live_resolved_http,
  (select count(*) from domains where verification_status = 'confirmed_ecommerce') as dom_confirmed,
  (select count(*) from domains where verification_status = 'probable_ecommerce')  as dom_probable;
\echo -- domain verification status distribution
select verification_status, current_resolution_status, count(*) from domains group by 1,2 order by 3 desc;
\echo -- stores (verified records) by ecommerce_status x activity
select ecommerce_status, activity_status, count(*) from stores group by 1,2 order by 3 desc;
select (select count(*) from products) products_total, (select count(*) from products where status='active') products_active,
       (select count(distinct store_id) from products) stores_with_products;
\echo -- new stores per day (first_verified_at)
select first_verified_at::date as day, count(*) from stores group by 1 order by 1;
\echo -- rechecks: verify jobs for domains that already had an earlier succeeded verify job
with v as (select domain_id, completed_at, result->>'status' st, status,
                  row_number() over (partition by domain_id order by coalesce(completed_at, scheduled_at)) n
           from crawl_jobs where job_type='verify')
select count(*) filter (where n>1) recheck_jobs,
       count(*) filter (where n>1 and status='succeeded') recheck_succeeded,
       count(*) filter (where n>1 and status='succeeded' and st in ('confirmed_ecommerce','probable_ecommerce')) recheck_still_store,
       count(*) filter (where n>1 and status='failed') recheck_failed
from v;
\echo -- job status totals
select job_type, status, count(*) from crawl_jobs group by 1,2 order by 1,2;

\echo ===== T2 SOURCES =====
\echo -- per source: records, unique domains, processed, outcome
with src as (
  select s.name source, u.domain_id from discovered_urls u join discovery_sources s on s.id=u.source_id
  union all
  select 'manual (no source record)', d.id from domains d where not exists (select 1 from discovered_urls u where u.domain_id=d.id)
), per as (select source, count(*) records, count(distinct domain_id) uniq from src group by 1)
select p.source, p.records, p.uniq unique_domains, p.records - p.uniq dup_records_within_source,
  count(*) filter (where d.verification_status <> 'pending') processed,
  count(*) filter (where d.verification_status = 'confirmed_ecommerce') confirmed,
  count(*) filter (where d.verification_status = 'probable_ecommerce') probable,
  count(*) filter (where st.activity_status = 'active') active_stores,
  count(*) filter (where d.verification_status in ('temporarily_unreachable','blocked')) unreachable,
  count(*) filter (where d.verification_status = 'not_ecommerce') not_ecom,
  count(*) filter (where d.verification_status = 'uncertain') uncertain
from per p join (select distinct source, domain_id from src) x on x.source = p.source
join domains d on d.id = x.domain_id left join stores st on st.domain_id = d.id
group by p.source, p.records, p.uniq order by 1;
\echo -- unique vs shared store contribution
with src as (select distinct s.name source, u.domain_id from discovered_urls u join discovery_sources s on s.id=u.source_id),
n as (select domain_id, count(*) nsrc from src group by 1)
select src.source,
  count(*) filter (where st.id is not null and n.nsrc=1) stores_unique_to_source,
  count(*) filter (where st.id is not null and n.nsrc>1) stores_shared,
  count(*) filter (where n.nsrc>1) domains_shared_with_other_sources
from src join n using (domain_id) left join stores st on st.domain_id = src.domain_id
group by 1 order by 1;
\echo -- stores with no discovery source (manual)
select count(*) manual_stores from stores st where not exists (select 1 from discovered_urls u where u.domain_id = st.domain_id);
\echo -- discovery runs per source
select s.name, r.status, count(*), sum(r.records_found) found, sum(r.records_processed) stored, sum(r.records_failed) invalid,
  round(avg(extract(epoch from (r.completed_at - r.started_at))/60)::numeric,1) avg_minutes
from discovery_runs r join discovery_sources s on s.id=r.source_id group by 1,2 order by 1,2;
select name, configuration, state from discovery_sources order by name;
\pset footer off
\echo ===== T3 WHY: latest verify result per domain, by outcome reason =====
with latest as (
  select distinct on (domain_id) domain_id, result from crawl_jobs
  where job_type='verify' and status='succeeded' order by domain_id, completed_at desc
)
select d.verification_status,
  case
    when l.result->'evidence'->>'reason' like 'robots.txt disallows%' then 'robots disallow'
    when l.result->'evidence'->>'reason' like 'homepage returned HTTP 403%' then 'HTTP 403 (bot wall/forbidden)'
    when l.result->'evidence'->>'reason' like 'homepage returned HTTP 429%' then 'HTTP 429 (rate limited)'
    when l.result->'evidence'->>'reason' like 'homepage returned HTTP 4%' then 'other HTTP 4xx'
    when l.result->'evidence'->>'reason' like 'homepage has almost no%' then 'empty/JS shell'
    when l.result->'evidence'->>'reason' like 'homepage is not HTML%' then 'not HTML'
    when l.result->>'resolution' = 'nxdomain' then 'DNS: no such domain'
    when l.result->>'resolution' = 'error' then 'network error after retries'
    when l.result->>'resolution' = 'blocked' then 'non-public IP'
    when (l.result->'evidence'->'score'->>'points') is not null then 'scored ' || (l.result->'evidence'->'score'->>'points') || ' pts'
    else coalesce(l.result->'evidence'->>'reason', 'other') end as reason,
  count(*)
from domains d join latest l on l.domain_id = d.id
where d.verification_status in ('uncertain','not_ecommerce','temporarily_unreachable','blocked')
group by 1,2 order by 1, 3 desc;

\echo -- redirect-to-other-host among non-stores (parked / moved domains)
with latest as (select distinct on (domain_id) domain_id, result from crawl_jobs where job_type='verify' and status='succeeded' order by domain_id, completed_at desc)
select d.verification_status, count(*) filter (where l.result->'evidence'->'homepage'->>'url' not like '%' || d.normalized_domain || '%') redirected_off_host, count(*) total
from domains d join latest l on l.domain_id=d.id where d.verification_status in ('not_ecommerce','uncertain','confirmed_ecommerce','probable_ecommerce') group by 1;

\echo -- not_ecommerce with SOME commerce signal (possible false negatives)
with latest as (select distinct on (domain_id) domain_id, result from crawl_jobs where job_type='verify' and status='succeeded' order by domain_id, completed_at desc)
select l.result->'evidence'->'score'->>'signals_hit' hits, count(*) from domains d join latest l on l.domain_id=d.id
where d.verification_status in ('not_ecommerce','uncertain') and l.result->'evidence'->'score' is not null group by 1 order by 2 desc limit 12;

\echo -- sample uncertain scored 2-3 (borderline) domains
with latest as (select distinct on (domain_id) domain_id, result from crawl_jobs where job_type='verify' and status='succeeded' order by domain_id, completed_at desc)
select d.normalized_domain, l.result->'evidence'->'score'->>'points' pts, l.result->'evidence'->'score'->>'signals_hit' hits,
       l.result->'evidence'->'homepage'->'signals'->>'title' title
from domains d join latest l on l.domain_id=d.id where d.verification_status='uncertain' and (l.result->'evidence'->'score'->>'points')::int between 2 and 3
order by random() limit 12;

\echo -- sample not_ecommerce scored 0-1 with titles
with latest as (select distinct on (domain_id) domain_id, result from crawl_jobs where job_type='verify' and status='succeeded' order by domain_id, completed_at desc)
select d.normalized_domain, l.result->'evidence'->'score'->>'points' pts, left(l.result->'evidence'->'homepage'->'signals'->>'title', 70) title,
       l.result->'evidence'->'homepage'->>'url' final_url
from domains d join latest l on l.domain_id=d.id where d.verification_status='not_ecommerce' order by random() limit 15;

\echo -- platform distribution of stores
select primary_platform, ecommerce_status, count(*) from stores group by 1,2 order by 3 desc;

\echo ===== QUEUE / MONITORING =====
select count(*) pending, min(first_seen_at)::timestamp(0) oldest, max(first_seen_at)::timestamp(0) newest,
       round(extract(epoch from now()-min(first_seen_at))/3600) oldest_age_h from domains where verification_status='pending';
\echo -- verifications per hour (last 12h)
select date_trunc('hour', completed_at) hr, count(*) filter (where job_type='verify') verify, count(*) filter (where job_type='products') products,
  round(avg(extract(epoch from completed_at-started_at)) filter (where job_type='verify')::numeric,1) verify_avg_s
from crawl_jobs where completed_at > now() - interval '12 hours' group by 1 order by 1;
\echo -- repeated verify jobs per domain within 24h (unnecessary repeats?)
select count(*) domains_with_2plus_verify_24h from (select domain_id from crawl_jobs where job_type='verify' and scheduled_at > now()-interval '24 hours' group by 1 having count(*)>1) x;
\echo -- store recheck schedule
select activity_status, count(*), min(next_check_at)::timestamp(0) next_min, max(next_check_at)::timestamp(0) next_max,
  count(*) filter (where next_check_at <= now() + interval '1 day') due_within_1d from stores group by 1;
\echo -- product re-extractions per store (repeats) and changes detected
select count(*) stores_extracted, sum(n) extraction_jobs, max(n) max_per_store from (select domain_id, count(*) n from crawl_jobs where job_type='products' group by 1) x;
select change_type, count(*) from product_observations group by 1;
select observation_type, count(*) from store_observations group by 1 order by 2 desc;
\echo -- suppression
select count(*) suppressed from domains where suppressed_at is not null;
