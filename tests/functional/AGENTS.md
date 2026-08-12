# Live functional-test safety

Follow [`../../AGENTS.md`](../../AGENTS.md) and read `README.md` in this
directory before running anything here.

The manual runner connects to Minecraft. Suites 100-1000 are admin/destructive,
Suite 1100 mutates persistent world state, and Suite 1200 is read-only but still
opens a live bridge connection. Listing tests is offline; executing them is not.
Never run an unfiltered selection or use a valued autonomous world. Mutating
selections require the disposable profile and exact expected-server guard from
the functional-test guide.
