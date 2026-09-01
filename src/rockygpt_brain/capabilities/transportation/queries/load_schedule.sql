SELECT v.version,
       v.activated_at,
       v.source_commit_sha,
       t.id::text AS trip_id,
       t.source_record_key,
       t.sequence,
       t.departure,
       t.arrival,
       t.stops,
       t.valid_from,
       t.valid_until,
       t.content_hash,
       r.name AS route,
       r.service_day,
       s.id::text AS source_id,
       s.title AS source_title,
       s.canonical_url AS source_url,
       s.trust_tier,
       s.freshness_sla_hours,
       t.collected_at
  FROM rockygpt_v2.dataset_versions v
  JOIN rockygpt_v2.shuttle_trips t ON t.dataset_version_id = v.id
  JOIN rockygpt_v2.shuttle_routes r
    ON r.id = t.route_id AND r.dataset_version_id = v.id
  JOIN rockygpt_v2.sources s ON s.id = t.source_id
 WHERE v.status = 'active'
 ORDER BY r.service_day, r.name, t.sequence
