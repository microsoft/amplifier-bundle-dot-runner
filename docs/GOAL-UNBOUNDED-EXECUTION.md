# Unbounded Goal traversal and truthful interruptions

Goal authors may set `graph [max_steps=0]` to remove the traversal ceiling in
both main and subgraph execution. Other graphs retain their existing implicit
ceiling when the attribute is absent. Positive values remain explicit ceilings;
negative and non-integer values are rejected.

This is an author-controlled extension to graph execution (attractor canonical
spec §3.2), not a replacement of cancellation, explicit execution deadlines,
resource isolation, or terminal verification. Loop-agent's existing
session-history turn counting remains unchanged (§2.5 of the coding-agent spec).

At the spawn boundary, an `incomplete` or `cancelled` lifecycle envelope cannot
be overridden by nonempty intermediate text or an interrupted success claim.
The node records a non-explicit failure instead. This preserves the completion
envelope contract recorded in EXTENSIONS §35 and explicit-verdict policy §25.
