-- capital_allocator_check_and_reserve.lua
-- Atomic budget + concurrency gate for Order Exec's pre-entry check.
-- Called by Order Exec just before placing an entry order; guarantees that
-- two simultaneous strategy threads cannot both succeed when capital is
-- only enough for one.
--
-- KEYS layout:
--   KEYS[1] = orders:allocator:deployed             (HASH; fields per index + total)
--   KEYS[2] = orders:allocator:open_count           (HASH; same)
--   KEYS[3] = orders:allocator:open_symbols         (SET)
--
-- ARGV:
--   ARGV[1] = index                                 (nifty50 / banknifty)
--   ARGV[2] = premium_required_inr                  (number)
--   ARGV[3] = trading_capital_inr                   (number)
--   ARGV[4] = max_concurrent_positions              (integer)
--
-- Returns:
--   {ok, reason, deployed_total_after, open_total_after}
--   ok = 1 → reservation succeeded; caller must release on abort/exit
--   ok = 0 → rejected; reason is one of:
--            'INSUFFICIENT_CAPITAL', 'MAX_CONCURRENT_REACHED', 'ALREADY_OPEN_ON_INDEX'

local deployed_key = KEYS[1]
local open_key     = KEYS[2]
local symbols_set  = KEYS[3]

local index             = ARGV[1]
local premium_required  = tonumber(ARGV[2])
local capital_total     = tonumber(ARGV[3])
local max_concurrent    = tonumber(ARGV[4])

if redis.call('SISMEMBER', symbols_set, index) == 1 then
  local dep = tonumber(redis.call('HGET', deployed_key, 'total')) or 0
  local cnt = tonumber(redis.call('HGET', open_key, 'total')) or 0
  return {0, 'ALREADY_OPEN_ON_INDEX', dep, cnt}
end

local current_total = tonumber(redis.call('HGET', open_key, 'total')) or 0
if current_total + 1 > max_concurrent then
  local dep = tonumber(redis.call('HGET', deployed_key, 'total')) or 0
  return {0, 'MAX_CONCURRENT_REACHED', dep, current_total}
end

local deployed_total = tonumber(redis.call('HGET', deployed_key, 'total')) or 0
if deployed_total + premium_required > capital_total then
  return {0, 'INSUFFICIENT_CAPITAL', deployed_total, current_total}
end

-- Reserve atomically
redis.call('HINCRBYFLOAT', deployed_key, index, premium_required)
redis.call('HINCRBYFLOAT', deployed_key, 'total', premium_required)
redis.call('HINCRBY',      open_key,     index, 1)
redis.call('HINCRBY',      open_key,     'total', 1)
redis.call('SADD',         symbols_set,  index)

local new_dep = tonumber(redis.call('HGET', deployed_key, 'total'))
local new_cnt = tonumber(redis.call('HGET', open_key, 'total'))
return {1, 'OK', new_dep, new_cnt}
