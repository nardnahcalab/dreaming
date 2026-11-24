"""
dream_memory - local AI memory capture and consolidation.

Pipeline:  collectors -> episodic store (append-only) -> dreaming -> dreams
           recall searches consolidated first, episodic second.

All data stays on this machine. Source tool databases are opened read-only.
"""
