import { useEffect, useState } from "react";

const METHODS = [
  { value: "hybrid", label: "Hybrid (BM25 + vector)" },
  { value: "bm25", label: "BM25 keyword" },
  { value: "vector", label: "Vector similarity" },
];

const EXAMPLES = [
  "What is the acknowledge and mitigation target for Severity 4 incidents?",
  "What was the root cause of the CDN outage?",
  "Combine evidence from 'EU cdn_issue incident report (2025-07-13)' and 'Cdn Config Note: operational notes'. What is the recommended mitigation approach?",
];

export default function App() {
  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [method, setMethod] = useState("hybrid");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [health, setHealth] = useState(null);
  const [feedbackSent, setFeedbackSent] = useState(false);

  // Show corpus size and backend so the user can see what is indexed.
  useEffect(() => {
    fetch("/readyz")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  async function ask(event) {
    event.preventDefault();
    if (!question.trim()) return;

    setLoading(true);
    setError(null);
    setResult(null);
    setFeedbackSent(false);

    try {
      const response = await fetch("/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, method, k: 5 }),
      });

      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `Request failed (${response.status})`);
      }

      setResult(await response.json());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function sendFeedback(helpful) {
    if (!result) return;
    try {
      await fetch("/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: result.question,
          answer: result.answer,
          helpful,
        }),
      });
      setFeedbackSent(true);
    } catch {
      setFeedbackSent(false);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>IT Operations AI Knowledge Assistant</h1>
        <p>
          Ask a question about incidents, policies, or architecture notes. Answers
          are grounded in the knowledge base and cite the passages they came from.
        </p>
      </header>

      <div className="status">
        <span className={`dot ${health ? "ok" : "err"}`} />
        {health
          ? `${health.documents.toLocaleString()} documents · ${health.chunks.toLocaleString()} chunks · ${health.retriever_backend} retrieval`
          : "Knowledge base unavailable"}
      </div>

      <form onSubmit={ask}>
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about an incident, SLA target, or architecture note…"
          aria-label="Question"
        />
        <button type="submit" disabled={loading || !question.trim()}>
          {loading ? "Searching…" : "Ask"}
        </button>
      </form>

      <div className="controls">
        <label htmlFor="method">Retrieval</label>
        <select
          id="method"
          value={method}
          onChange={(e) => setMethod(e.target.value)}
        >
          {METHODS.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
        <span>·</span>
        <span>Try: {EXAMPLES.length} example questions</span>
      </div>

      {error && <p className="error">{error}</p>}

      {result && (
        <>
          <section className="card">
            <h2>Answer</h2>
            <p className="answer">{result.answer}</p>

            <div>
              <span className={`badge ${result.grounded ? "grounded" : "refused"}`}>
                {result.grounded ? "Grounded" : "Insufficient evidence"}
              </span>
              <span className="badge">{result.method}</span>
              <span className="badge">{result.latency_ms} ms</span>
            </div>

            {result.citations.length > 0 && (
              <>
                <h2 style={{ marginTop: 18 }}>Citations</h2>
                <div className="citations">
                  {result.citations.map((c) => (
                    <span className="citation" key={c}>
                      {c}
                    </span>
                  ))}
                </div>
              </>
            )}

            <div className="feedback" style={{ marginTop: 18 }}>
              <span>Was this helpful?</span>
              <button onClick={() => sendFeedback(true)} disabled={feedbackSent}>
                Yes
              </button>
              <button onClick={() => sendFeedback(false)} disabled={feedbackSent}>
                No
              </button>
              {feedbackSent && <span>Thanks — recorded.</span>}
            </div>
          </section>

          {result.evidence.length > 0 && (
            <section className="card">
              <h2>Retrieved evidence</h2>
              {result.evidence.map((e) => (
                <div className="evidence-item" key={e.chunk_id}>
                  <div className="evidence-head">
                    <span className="evidence-title">{e.title}</span>
                    <span>{e.chunk_id}</span>
                  </div>
                  <p className="evidence-text">{e.text}</p>
                </div>
              ))}
            </section>
          )}
        </>
      )}
    </div>
  );
}
