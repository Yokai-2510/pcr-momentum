"""Strategy implementations live under this package.

Each strategy is a directory with:
  - strategy.py        — the Strategy subclass exported here
  - any helper modules (basket, metrics, decisions, etc.)

Strategies are discovered by `engines.strategy.registry` based on the
`strategy:registry` SET in Redis populated by Init from the
`strategy_definitions` Postgres table.
"""
