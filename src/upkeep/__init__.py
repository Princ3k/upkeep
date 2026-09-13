"""upkeep — self-maintaining API migrations.

Pipeline: detect -> normalize -> index -> plan -> patch -> verify -> deliver.
Each stage is pure and independently testable; `upkeep run` chains them.
"""

__version__ = "0.0.1"
