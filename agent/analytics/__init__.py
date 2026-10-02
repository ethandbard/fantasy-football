"""
The in-house data analyst: usage data from nflverse, an expected-points
model, forecasts with ranges, charts, and a Quarto PDF report.

sources fetches and caches the public data, model is pure pandas and numpy
so tests can feed it frames, charts draws the figures, report renders the
PDF, and pipeline ties them to one run's view of the league.
"""
