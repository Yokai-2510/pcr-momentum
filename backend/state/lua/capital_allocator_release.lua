-- capital_allocator_release.lua
-- Atomic release of a previously-reserved budget + concurrency slot.
-- Symmetric counterpart to capital_allocator_check_and_reserve.lua.
--
-- Called by Order Exec from:
--   * cleanup path (Stage F) after a position closes.
--   * abort path when an entry order fails after the reservation succeeded.
--
-- KEYS layout (must mirror the reserve script):
--   KEYS[1] = orders:allocator:deployed         (HASH)
--   KEYS[2] = orders:allocator:open_count       (HASH)
--   KEYS[3] = orders:allocator:open_symbols     (SET)
--
-- ARGV:
--   ARGV[1] = index                             (nifty50 / banknifty)
--   ARGV[2] = premium_to_release_inr            (number; what was reserved)
--
-- Returns:
--   {ok, reason, deployed_total_after, open_total_after}
--   ok = 1 → released
--   ok = 0 → no-op (no reservation present)

local deployed_key = KEYS[1]
local open_key     = KEYS[2]
local symbols_set  = KEYS[3]

local index    = ARGV[1]
local premium  = tonumber(ARGV[2]) or 0

-- Idempotent: if the symbol isn't in the set, treat as already released.
if redis.call('SISMEMBER', symbols_set, index) ~= 1 then
  local dep = tonumber(redis.call('HGET', deployed_key, 'total')) or 0
  local cnt = tonumber(redis.call('HGET', open_key, 'total')) or 0
  return {0, 'NOT_RESERVED', dep, cnt}
end

-- Decrement deployed (clamp at 0)
local dep_idx = tonumber(redis.call('HGET', deployed_key, index)) or 0
local dep_tot = tonumber(redis.call('HGET', deployed_key, 'total')) or 0
local rel_idx = math.min(dep_idx, premium)
local rel_tot = math.min(dep_tot, premium)
if rel_idx > 0 then
  redis.call('HINCRBYFLOAT', deployed_key, index, -rel_idx)
end
if rel_tot > 0 then
  redis.call('HINCRBYFLOAT', deployed_key, 'total', -rel_tot)
end

-- Decrement open_count (clamp at 0)
local open_idx = tonumber(redis.call('HGET', open_key, index)) or 0
local open_tot = tonumber(redis.call('HGET', open_key, 'total')) or 0
if open_idx > 0 then
  redis.call('HINCRBY', open_key, index, -1)
end
if open_tot > 0 then
  redis.call('HINCRBY', open_key, 'total', -1)
end

redis.call('SREM', symbols_set, index)

local new_dep = tonumber(redis.call('HGET', deployed_key, 'total')) or 0
local new_cnt = tonumber(redis.call('HGET', open_key, 'total')) or 0
return {1, 'OK', new_dep, new_cnt}
