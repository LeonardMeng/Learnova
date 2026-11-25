# Grading rubric

Learnova grades every quiz answer on a 0–5 scale with one decimal place. The grading prompt loads
this file each time it grades an answer, so editing this file changes grading directly. Keep the
level headings (`## 0` to `## 5`); the descriptions and examples can be changed freely.

## How a score is given

1. Decide which level below the answer fits best, judged against the question and the student's
   own notes.
2. Use the decimal to place the answer between that level and the next one up or down. The score
   always rounds to the chosen level: a level-3 answer scores from 2.5 to 3.5, where 2.5 means
   "level 3, close to 2" and 3.5 means "level 3, close to 4".
3. Scores are stored rounded to one decimal. Mastery uses score / 5.

Judge understanding, not wording: an answer in the student's own words that shows the idea is
correct. Spelling, grammar and length do not matter unless they change the meaning.

## Example question used below

> Why does Redis offer both RDB snapshots and AOF logging for persistence?

## 0: No answer or wrong

Blank, "I don't know", off-topic, or entirely incorrect.

> Example: "Redis saves everything to disk on every write, so it doesn't need either."

## 1: Major misconception

Mentions the right topic but the main idea is wrong, or the only correct part is incidental.

> Example: "RDB is for reading and AOF is for writing."

## 2: Partly correct

Gets part of the core idea right but leaves out or confuses an essential part.

> Example: "RDB takes snapshots of the data. AOF is another way to save data."

## 3: Mostly correct, with gaps

The core idea is right, but an important detail or the reason behind it is missing or vague.

> Example: "RDB saves periodic snapshots and AOF logs every write. AOF loses less data."

## 4: Correct, small omissions

Correct and clear; only minor details or nuances are missing.

> Example: "RDB snapshots are compact and restart fast but can lose the writes since the last
> snapshot. AOF logs every write, so it loses at most about a second of data, but its files are
> bigger and replay is slower."

## 5: Complete and precise

Fully correct, covers the key points and the trade-off, with nothing important missing.

> Example: "RDB: point-in-time snapshots, compact, fast restarts, but you can lose everything
> since the last snapshot. AOF: append-only log of writes with a configurable fsync (e.g. every
> second), so little data is lost, but files are larger and restarts slower; it is rewritten in
> the background to stay small. They are offered together because they trade durability against
> speed and size, and they can be combined (RDB for backups, AOF for durability)."
