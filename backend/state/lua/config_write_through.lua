-- config_write_through.lua
-- Atomic Redis SET of a `strategy:configs:{section}` key + a UI-dirty mark,
-- called by FastAPI right after a Postgres `config_settings` UPDATE/INSERT
-- commits. Keeps Redis and the dashboard view in sync without a race window.
--
-- KEYS layout:
--   KEYS[1] = strategy:configs:{section}            (JSON STRING)
--   KEYS[2] = ui:dirty                              (SET)
--   KEYS[3] = ui:pub:view                           (PUB/SUB channel)
--
-- ARGV:
--   ARGV[1] = JSON string to SET
--   ARGV[2] = view name to mark dirty (e.g. 'configs')
--
-- Returns: 1 if the SET happened (always, idempotent), else 0.

local cfg_key   = KEYS[1]
local dirty_set = KEYS[2]
local pub_chan  = KEYS[3]

local payload   = ARGV[1]
local view_name = ARGV[2]

redis.call('SET', cfg_key, payload)
redis.call('SADD', dirty_set, view_name)
redis.call('PUBLISH', pub_chan, 'ui:views:' .. view_name)

return 1
