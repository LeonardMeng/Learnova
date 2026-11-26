import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { api, ApiError, type QuizKind } from "../api/client";
import { ErrorBox, MasteryBar, ScoreBadge, Spinner, WorkspacePicker } from "../components/ui";
import { loadSession, newSession, saveSession, type Session } from "../quiz/session";
import { useWorkspace } from "../workspace";

const KINDS: { value: QuizKind; label: string; hint: string }[] = [
  { value: "short", label: "Short answer", hint: "Answer in your own words; the model grades it." },
  { value: "mc", label: "Multiple choice", hint: "Pick A–D; checked instantly." },
  { value: "tf", label: "True / false", hint: "Checked instantly; counts least toward mastery." },
];
const LETTERS = "ABCD";

export function QuizPage() {
  const [workspace] = useWorkspace();
  const [session, setSessionState] = useState<Session | null>(() => loadSession());
  const [busy, setBusy] = useState<null | "question" | "grading">(null);
  const [error, setError] = useState<unknown>(null);
  const queryClient = useQueryClient();

  const setSession = (next: Session | null) => {
    saveSession(next);
    setSessionState(next);
  };

  async function fetchQuestion(s: Session) {
    setBusy("question");
    setError(null);
    try {
      const q = await api.issueQuestion(s.kind, s.workspace, s.asked);
      setSession({ ...s, current: q, result: null, asked: [...s.asked, q.point_id] });
    } catch (e) {
      if (e instanceof ApiError && e.code === "NoPointsLeft") {
        setSession({ ...s, current: null, finished: true, endMessage: e.message });
      } else {
        setSession(s);
        setError(e);
      }
    } finally {
      setBusy(null);
    }
  }

  async function submit(answer: string) {
    if (!session?.current) return;
    setBusy("grading");
    setError(null);
    try {
      const a = await api.answer(session.current.id, answer);
      const row = { point: a.point.name, score: a.score, before: a.mastery_before, after: a.mastery_after };
      setSession({ ...session, result: a, rows: [...session.rows, row] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
      queryClient.invalidateQueries({ queryKey: ["points"] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  async function skip() {
    if (!session?.current) return;
    setError(null);
    try {
      await api.skip(session.current.id);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409)) return setError(e);
    }
    const row = { point: "(skipped)", score: null, before: 0, after: 0 };
    advance({ ...session, rows: [...session.rows, row] });
  }

  function advance(s: Session) {
    if (s.number >= s.count) return setSession({ ...s, current: null, result: null, finished: true });
    const next = { ...s, number: s.number + 1, current: null, result: null };
    setSession(next);
    void fetchQuestion(next);
  }

  if (!session) {
    return <StartForm workspace={workspace} onStart={(s) => { setSession(s); void fetchQuestion(s); }} />;
  }
  if (session.finished) {
    return <Summary session={session} onRestart={() => setSession(null)} />;
  }

  return (
    <section className="quiz">
      <div className="quiz-head">
        <h1>
          Question {session.number} of {session.count}
        </h1>
        <button className="link" onClick={() => setSession({ ...session, finished: true, current: null })}>
          End quiz
        </button>
      </div>
      {session.current?.source && <p className="muted">from {session.current.source}</p>}
      {error != null && <ErrorBox error={error} onRetry={session.current ? undefined : () => fetchQuestion(session)} />}
      {busy === "question" && <Spinner label="Writing a question…" />}
      {!busy && !session.current && error == null && (
        <button onClick={() => fetchQuestion(session)}>Continue</button>
      )}
      {session.current && !session.result && (
        <QuestionCard key={session.current.id} session={session} busy={busy === "grading"} onSubmit={submit} onSkip={skip} />
      )}
      {busy === "grading" && <Spinner label="Grading…" />}
      {session.result && (
        <ResultCard session={session} onNext={() => advance(session)} />
      )}
    </section>
  );
}

function StartForm({ workspace, onStart }: { workspace: string | null; onStart: (s: Session) => void }) {
  const [kind, setKind] = useState<QuizKind>("short");
  const [count, setCount] = useState("5"); // the text as typed; checked when starting
  const n = Math.min(50, Math.max(1, Math.floor(Number(count)) || 0));
  return (
    <section className="card start">
      <div className="page-head">
        <h1>Quiz</h1>
        <WorkspacePicker />
      </div>
      <fieldset>
        <legend>Question type</legend>
        {KINDS.map((k) => (
          <label key={k.value} className="radio">
            <input type="radio" name="kind" value={k.value} checked={kind === k.value} onChange={() => setKind(k.value)} />
            <span>
              {k.label} <span className="muted">· {k.hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <label className="field">
        Questions
        <input type="number" min={1} max={50} value={count} onChange={(e) => setCount(e.target.value)} />
      </label>
      <p className="muted">From: {workspace ?? "all workspaces"} (change it in the header)</p>
      <button disabled={!n} onClick={() => onStart(newSession(kind, n, workspace))}>
        Start quiz
      </button>
    </section>
  );
}

function QuestionCard({ session, busy, onSubmit, onSkip }: {
  session: Session; busy: boolean; onSubmit: (answer: string) => void; onSkip: () => void;
}) {
  const q = session.current!;
  const [text, setText] = useState("");
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (q.type === "short") textRef.current?.focus();
    if (q.type === "short") return;
    const onKey = (e: KeyboardEvent) => {
      if (busy || e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toUpperCase();
      if (q.type === "mc" && LETTERS.slice(0, q.options?.length ?? 4).includes(k)) onSubmit(k);
      if (q.type === "tf" && (k === "T" || k === "F")) onSubmit(k);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [q, busy, onSubmit]);

  return (
    <div className="card question">
      <p className="question-text">{q.type === "tf" ? `True or false: ${q.text}` : q.text}</p>
      {q.type === "short" && (
        <>
          <textarea
            ref={textRef}
            aria-label="Your answer"
            rows={6}
            value={text}
            disabled={busy}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) onSubmit(text);
            }}
            placeholder="Your answer… (Ctrl/⌘ + Enter to submit)"
          />
          <div className="actions">
            <button disabled={busy || !text.trim()} onClick={() => onSubmit(text)}>
              Submit
            </button>
            <button className="secondary" disabled={busy} onClick={onSkip}>
              Skip
            </button>
          </div>
        </>
      )}
      {q.type === "mc" && (
        <div className="options">
          {q.options?.map((option, i) => (
            <button key={i} className="option" disabled={busy} onClick={() => onSubmit(LETTERS[i])}>
              <span className="key">{LETTERS[i]}</span> {option}
            </button>
          ))}
          <button className="secondary" disabled={busy} onClick={onSkip}>
            Skip
          </button>
        </div>
      )}
      {q.type === "tf" && (
        <div className="actions">
          <button className="option" disabled={busy} onClick={() => onSubmit("t")}>
            <span className="key">T</span> True
          </button>
          <button className="option" disabled={busy} onClick={() => onSubmit("f")}>
            <span className="key">F</span> False
          </button>
          <button className="secondary" disabled={busy} onClick={onSkip}>
            Skip
          </button>
        </div>
      )}
    </div>
  );
}

function ResultCard({ session, onNext }: { session: Session; onNext: () => void }) {
  const r = session.result!;
  const last = session.number >= session.count;
  return (
    <div className="card result">
      <p className="question-text">{session.current?.text}</p>
      {r.correct != null && (
        <p className={r.correct ? "good" : "bad"}>
          {r.correct ? "Correct." : `Not quite: the answer is ${r.correct_option}.`}
        </p>
      )}
      <div className="result-head">
        <ScoreBadge score={r.score} />
        <strong>{r.point.name}</strong>
        <span className="muted">mastery</span>
        <MasteryBar value={r.mastery_before} /> → <MasteryBar value={r.mastery_after} />
      </div>
      {r.feedback && <p>{r.feedback}</p>}
      {r.missing && (
        <p>
          <strong>Missing:</strong> {r.missing}
        </p>
      )}
      {r.explanation && <p>{r.explanation}</p>}
      {r.reference_answer && (
        <details>
          <summary>Reference answer</summary>
          <p>{r.reference_answer}</p>
        </details>
      )}
      <button autoFocus onClick={onNext}>
        {last ? "See summary" : "Next question"}
      </button>
    </div>
  );
}

function Summary({ session, onRestart }: { session: Session; onRestart: () => void }) {
  const answered = session.rows.filter((r) => r.score !== null);
  const average = answered.length ? answered.reduce((a, r) => a + (r.score ?? 0), 0) / answered.length : null;
  return (
    <section className="card">
      <h1>Quiz finished</h1>
      {session.endMessage && <p className="muted">{session.endMessage}</p>}
      <p>
        {answered.length} answered, {session.rows.length - answered.length} skipped
        {average !== null && <> · average {average.toFixed(1)} / 5</>}
      </p>
      {session.rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Point</th>
              <th>Score</th>
              <th>Mastery</th>
            </tr>
          </thead>
          <tbody>
            {session.rows.map((r, i) => (
              <tr key={i}>
                <td>{r.point}</td>
                <td>{r.score === null ? "skipped" : <ScoreBadge score={r.score} />}</td>
                <td>{r.score === null ? "–" : `${r.before.toFixed(2)} → ${r.after.toFixed(2)}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="actions">
        <button onClick={onRestart}>Start another quiz</button>
        <Link to="/points">See your points</Link>
      </div>
    </section>
  );
}
