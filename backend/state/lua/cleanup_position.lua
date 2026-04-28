-- cleanup_position.lua
-- Atomic teardown of a position's Redis footprint after it closes.
--
-- KEYS layout (Modular_Design.md §8):
--   KEYS[1] = orders:positions:{pos_id}
--   KEYS[2] = orders:positions:open                 (SET)
--   KEYS[3] = orders:positions:open_by_index:{idx}  (SET)
--   KEYS[4] = orders:positions:closed_today         (SET)
--   KEYS[5] = orders:status:{pos_id}                (HASH)
--   KEYS[6] = strategy:{idx}:current_position_id    (STRING)
--   KEYS[7] = strategy:signals:active               (SET)
--   KEYS[8] = strategy:signals:{sig_id}             (JSON STRING)
--
-- ARGV:
--   ARGV[1] = pos_id
--   ARGV[2] = sig_id
--   ARGV[3..N] = order_ids to drop from `orders:orders:*` and broker maps
--                (passed as variadic args; iterated below)
--
-- Returns: number of keys deleted.

local pos_key      = KEYS[1]
local open_set     = KEYS[2]
local idx_open_set = KEYS[3]
local closed_set   = KEYS[4]
local status_key   = KEYS[5]
local cur_pos_key  = KEYS[6]
local active_sigs  = KEYS[7]
local signal_key   = KEYS[8]

local pos_id = ARGV[1]
local sig_id = ARGV[2]

local deleted = 0

-- 1. Membership flips
redis.call('SREM', open_set, pos_id)
redis.call('SREM', idx_open_set, pos_id)
redis.call('SADD', closed_set, pos_id)
redis.call('SREM', active_sigs, sig_id)

-- 2. Drop the per-strategy "current pos" pointer
deleted = deleted + redis.call('DEL', cur_pos_key)

-- 3. Drop the position HASH + its progress HASH + its signal payload
deleted = deleted + redis.call('DEL', pos_key)
deleted = deleted + redis.call('DEL', status_key)
deleted = deleted + redis.call('DEL', signal_key)

-- 4. Drop each order_id's hashes (entry + exit) and broker mirrors
for i = 3, #ARGV do
  local oid = ARGV[i]
  deleted = deleted + redis.call('DEL', 'orders:orders:' .. oid)
  deleted = deleted + redis.call('DEL', 'orders:broker:pos:' .. oid)
  redis.call('SREM', 'orders:broker:open_orders', oid)
end

return deleted
