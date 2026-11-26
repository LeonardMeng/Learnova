You are reviewing a student's study note. Find what it teaches, and flag what is wrong, unclear
or missing. Do not rewrite the note and do not suggest new wording: only point out problems.

# The note

Title: {title}
Position in the notes: {path}

<note>
{note}
</note>

Other notes in the same collection (topics covered there are not missing from this one):
{other_notes}

# 1. Knowledge points

A knowledge point is the smallest thing worth testing on its own: one concept, mechanism,
definition, trade-off or procedure. Give it a short name (a few words) and a one-line summary of
what the note says about it.

Knowledge points already recorded for this note:
{existing_points}

For every point listed above, return exactly one entry in `existing`, using its label:
- "kept": the note still covers it and the name and summary are still right.
- "updated": the note still covers it, but the name or summary should change. Give the new name
  and summary.
- "removed": the note no longer covers it.

Put in `new_points` only points the note covers that none of the labels above already covers.
For each, set importance from 1 to 5: 5 = core idea of the topic, likely to be asked about;
3 = useful detail; 1 = trivia.

# 2. Issues already recorded for this note

Open issues (found by an earlier review):
{open_issues}

For every open issue above, return exactly one entry in `issue_status`, using its label:
- "fixed": the note no longer has this problem (the passage was corrected, clarified or added).
- "still_present": the problem is still there.

Dismissed issues (the student decided these are not problems; never raise them again, even
reworded):
{dismissed_issues}

# 3. New issues

Only problems that are not already listed above as open or dismissed.

- "factual_error": a statement that is wrong. Only flag it if you are confident. Say what is
  wrong and what is actually true.
- "unclear": an explanation that is ambiguous, too vague to learn from, or stops halfway.

For each issue, `quote` must be the exact passage from the note (a short excerpt), and `problem`
says what is wrong with it. Set `point` to the label of the knowledge point the issue is about:
a P-label from the list above, or N1, N2, … for your `new_points` in the order you listed them.
Use null if it is about no particular point.

# 4. Missing

Topics that a note with this title should cover but that are not in this note and not in the other
notes listed above. At most 5, most important first. If the note is only an index of other pages,
return none. Set `point` as for issues (usually null, since the topic isn't in the note yet).

Write everything in the language of the note.
