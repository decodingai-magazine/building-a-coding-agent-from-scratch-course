# Shared fixtures for the integration suite. Mirrors src/ at the package boundary, not 1:1 —
# integration tests drive the whole stack, so their fixtures are about isolating the real
# filesystem seams (the session log dir, the web transport) rather than one module.
