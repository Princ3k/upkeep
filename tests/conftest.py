# The consumer fixture is deliberately broken against the provider's current
# surface — it is input to the pipeline, not part of upkeep's own suite.
collect_ignore_glob = ["fixtures/consumer/*"]
