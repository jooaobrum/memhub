#!/bin/sh
# usage: pilot/dump.sh <prefix>  -- ledger by user, then dropped candidates
P=${1:-habitantes_fake5}
q(){ docker compose exec -T postgres psql -U memhub -d memhub -At -F' | ' -c "$1"; }
q "select right(user_id,1) u, type, coalesce(payload->>'key',''), status, (conflicts_with is not null)::int cf, version v, left(content,170) from ${P}_memory m where type not in ('area') and status<>'superseded' and version=(select max(version) from ${P}_memory x where x.memory_id=m.memory_id) order by 1,2,3, observed_at"
echo --- DROPPED
q "select right(r.user_id,1), d->>'reason', d->'candidate'->>'type', left(d->'candidate'->'fields'->>'content',120) from ${P}_memory_runs r, jsonb_array_elements(r.dropped::jsonb) d order by 1"
