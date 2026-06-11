"""gexwheel: gamma-wall driven wheel-entry alerting pipeline.

Pipeline (see README.md):
  Stage 1 discovery (WSB mention velocity) ->
  Stage 2 hard filters (liquidity / VRP / price / regime / exclusions) ->
  watchlist DB ->
  GEX walls per ticker ->
  Discord alerts when spot approaches a qualifying put wall.
"""
__version__ = "0.1.0"
