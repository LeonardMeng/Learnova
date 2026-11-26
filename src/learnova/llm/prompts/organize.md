You are organizing a student's knowledge points into a hierarchy, so related points sit
together under topics. Each point below has a label, its name and summary, and in brackets
where it is now.

# The points to organize

{points}

# Topics elsewhere (you may put points under them, but don't move them)

{topics}

# The rules

- A point lives under at most one parent (its primary parent), or at the top level. Moving it
  replaces its primary parent.
- A point may also appear under up to 2 other parents as shortcuts, when it is used in or part
  of another topic as well ("X is used in Y"). Don't add a shortcut where the primary parent
  already says it.
- No loops: a point can't end up under itself, directly or further down.
- At most 4 levels: a top-level topic, then up to 3 levels below it.
- A point with other points under it becomes a topic and is no longer quizzed on its own, so only
  put points under a point that is genuinely a broader topic of theirs.

# What to propose

- `new_topics`: broader topics worth adding because several points belong together and no
  existing point covers them. Give each a label X1, X2, …, a short name, a one-line summary and
  an importance from 1 to 5. Only propose a topic if you also move at least two points under it.
- `moves`: points whose primary parent should change. `point` is a K- or X-label; `parent` is a
  K-, T- or X-label, or null for the top level. Leave out points that are already in a good place.
- `shortcuts`: `add` or `remove` a shortcut between `point` and `parent`.

Prefer a few clear groups over a deep or fine-grained tree. If the points are already well
organized, return empty lists. Write names and summaries in the language of the points.
