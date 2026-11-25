You are grading a student's answer to a quiz question about their own study notes. Use the
rubric below exactly; it defines the levels and how to use the decimal.

<rubric>
{rubric}
</rubric>

Knowledge point: {point_name}

The student's notes on it (the reference for what is correct):

{notes}

Question: {question}

Student's answer:
<answer>
{answer}
</answer>

Grade it:
1. `level`: the rubric level (0–5) the answer fits best.
2. `score`: the score with one decimal place, placed within that level as the rubric says.
3. `feedback`: two or three sentences to the student: what was right, what was wrong.
4. `missing`: the key ideas the answer left out, or an empty string if none.
5. `reference_answer`: a model answer in a few sentences, based on the notes.

If the notes themselves are wrong about something, grade against what is actually correct and say
so in the feedback. Write in the language of the notes.
