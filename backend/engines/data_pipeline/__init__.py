"""
engines.data_pipeline — always-on tick ingestion + aggregation engine.

Phase 5 implementation. See:
  - Project_Plan.md §Phase 5
  - Sequential_Flow.md §10 (pre-market subscribe), §17.3 (restart mid-day)
  - TDD.md §4 (module breakdown)
  - Schema.md §1.3 (market_data:* keys)

Module map:
  - state.py                  — DataPipelineState (cross-loop mutable state)
  - parser.py                 — parse_tick(raw_frame) → list[ParsedTick]
  - aggregator.py             — pure helpers + Redis writers
  - ws_io.py                  — broker WS owner; sync→async bridge
  - tick_processor.py         — drain queue → Redis (single-writer per index)
  - subscription_manager.py   — desired-set bootstrap + ATM-shift watcher
  - pre_market_subscriber.py  — first-frame gate
  - main.py                   — orchestrator (asyncio.gather)
  - __main__.py               — `python -m engines.data_pipeline`

The Strategy and Background engines wait on
`system:flags:data_pipeline_subscribed` before reading the option_chain
template (Sequential_Flow §10), so this engine MUST set that flag exactly
once per day after subscribing.
"""
