# F-07: Web Dashboard Alert Engine — Tasks

## Backend
- [x] 1. `backend/app/api/schemas.py` — Pydantic response schemas
- [x] 2. `backend/app/use_cases/dashboard_query.py` — Query layer (Clean Architecture)
- [x] 3. `backend/app/api/dashboard_routes.py` — 6 REST endpoints
- [x] 4. `backend/app/websocket/manager.py` — Upgrade full event publisher
- [x] 5. `backend/app/use_cases/safety_check_gate.py` — Update event format ke structured
- [x] 6. `backend/app/api/routes.py` — Include dashboard_routes
- [x] 7. `backend/app/main.py` — Wire initial_state + event types

## Frontend
- [x] 8. `frontend/src/store/useStore.ts` — Extend state (systemStatus, walletCandidates, notifications)
- [x] 9. `frontend/src/services/api.ts` — Extend 6 dashboard endpoints
- [x] 10. `frontend/src/hooks/useWebSocket.ts` — Exponential backoff + new handlers
- [x] 11. `frontend/src/views/Dashboard.tsx` — Full rebuild (chart + wallet approval + alerts)

# F-12: Dynamic Wallet Discovery — Tasks
- [x] 12. `backend/app/domain/models.py` — Add status field to WatchlistWallet
- [x] 13. `backend/app/infrastructure/database/models.py` — Add status column to WatchlistWalletORM
- [x] 14. `backend/app/infrastructure/database/repository.py` — Update SQLAlchemyWalletRepository mapping
- [x] 15. `backend/app/use_cases/wallet_discovery.py` — Implement WalletDiscoveryService
- [x] 16. `backend/app/use_cases/relevance_filter.py` — Hook WalletDiscoveryService to RelevanceFilter
- [x] 17. `backend/app/use_cases/monitor_wallets.py` — Implement reload_watchlist Orchestrator flow
- [x] 18. `backend/app/api/dashboard_routes.py` — Implement candidate endpoints & approval hot reload
- [x] 19. `backend/app/main.py` — Instantiate, start, and wire WalletDiscoveryService
- [x] 20. `backend/app/tests/test_wallet_discovery.py` — Write unit tests for discovery service

# F-13: Token Age & Liquidity Hard Filter — Tasks
- [x] 21. `backend/app/domain/models.py` — Add HardFilterAuditLog model
- [x] 22. `backend/app/infrastructure/database/models.py` — Add HardFilterAuditLogORM database model
- [x] 23. `backend/app/infrastructure/database/repository.py` — Implement SQLAlchemyHardFilterLogRepository
- [x] 24. `backend/app/use_cases/hard_filter.py` — Implement TokenAgeLiquidityHardFilter use case
- [x] 25. `backend/app/use_cases/relevance_filter.py` — Route relevant events through HardFilter
- [x] 26. `backend/app/main.py` — Wire HardFilter and SQLAlchemyHardFilterLogRepository on startup
- [x] 27. `backend/simulate.py` — Wire HardFilter in simulation execution environment
- [x] 28. `backend/app/tests/test_hard_filter.py` — Write unit tests verifying age, liquidity checks, cache TTL, and fail-closed logic

# F-14: Dedup & Cooldown per (Wallet, Token) — Tasks
- [x] 29. `backend/app/use_cases/trigger_engine.py` — Implement dynamic position-based and pending-timeout cooldown logic
- [x] 30. `backend/app/main.py` — Inject position_repo into TriggerEngine on startup
- [x] 31. `backend/simulate.py` — Inject position_repo into TriggerEngine in simulation script
- [x] 32. `backend/app/tests/test_trigger_engine.py` — Fix legacy test to match 5-minute pending window
- [x] 33. `backend/app/tests/test_cooldown.py` — Write unit tests for position-based cooldown logic, timeout, and cleanup

# F-15: RPC Fallback Primary/Secondary — Tasks
- [x] 34. `backend/config.yaml` — Add config fields for primary_url, secondary_url, and max_retry
- [x] 35. `backend/app/core/config.py` — Parse RPC fallback settings parameters from config.yaml
- [x] 36. `backend/app/domain/models.py` — Add RpcFailoverEvent schema model
- [x] 37. `backend/app/blockchain/monitor.py` — Implement reconnection failovers, health checks loop, recovery and WS notifications
- [x] 38. `backend/app/use_cases/trigger_engine.py` — Disable new transaction triggers in degraded mode
- [x] 39. `backend/app/execution/executor.py` — Switch L3 polling dynamically to 30s in degraded mode and 2s in normal mode
- [x] 40. `backend/app/use_cases/dashboard_query.py` — Expose dynamic RPC status and description to dashboard queries
- [x] 41. `frontend/src/hooks/useWebSocket.ts` — Add frontend WS message listener for rpc failover, degraded mode and recovery alerts
- [x] 42. `backend/app/tests/test_trigger_engine.py` — Reset degraded_mode class variable to isolate tests
- [x] 43. `backend/app/tests/test_rpc_fallback.py` — Write unit tests for fallback scenarios, health checking recovery, and poll sleep interval

# F-16: Position Correlation Cap — Tasks
- [x] 44. `backend/config.yaml` — Add max_concurrent_positions parameter to risk block
- [x] 45. `backend/app/core/config.py` — Parse max_concurrent_positions config with fallback warning log
- [x] 46. `backend/app/use_cases/auto_trade_executor.py` — Implement database query fail-safe, envelope WS broadcast and warning audit logging
- [x] 47. `frontend/src/hooks/useWebSocket.ts` — Handle position_cap_reached WS event to display info notification toast
- [x] 48. `backend/app/tests/test_auto_trade_executor.py` — Add unit tests verifying correlation limit block, database query failure fail-safe and WS event payload

# F-17: State Persistence & Crash Recovery — Tasks
- [x] 49. `backend/app/infrastructure/blockchain/token_service.py` — Update SolanaTokenInfoService to parse base token price_usd from DexScreener API
- [x] 50. `backend/app/use_cases/crash_recovery.py` — Create CrashRecoveryService class with startup retrain checks, database health verification, open positions price evaluations, stop-loss exits, and protective tasks re-activation
- [x] 51. `backend/app/main.py` — Integrate SQLite database health validation query text('SELECT 1'), backup recovery, and recovery_service run_recovery() inside FastAPI startup lifespan
- [x] 52. `watchdog.py` — Implement watchdog script that launches, restarts, logs stack traces, and records crash time / restart counts
- [x] 53. `backend/app/tests/test_crash_recovery.py` — Add unit tests covering recovery steps, database backup failovers, emergency exits, and fetch failure states
