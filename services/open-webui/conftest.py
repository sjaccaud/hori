# Gitignored live-API probes bound to this rig (tailnet URL, default
# creds) — useful manually, but they hit a live OWUI instance and pin
# stale API semantics, so they are not part of the test suite.
collect_ignore = [
    "test_prompts.py",
    "test_model_names.py",
    "test_local_tools.py",
]
