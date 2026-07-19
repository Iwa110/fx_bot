"""optimizer/loop - Grid AI-loop Phase 0 infrastructure (ledger, gates, evaluation).

See optimizer/grid_loop_engineering_design.md sec 7 for the design this
package implements. Core BT (grid_floatstop_bt.py) is read-only from here;
hash_guard.verify() enforces that at every evaluate_candidate.py invocation.
"""
