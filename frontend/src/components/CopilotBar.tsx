"use client";

import { memo, useEffect, useRef } from "react";
import { Bot, Mic, Send } from "lucide-react";
import { FormEvent, useState } from "react";
import IssueBadge from "@/components/IssueBadge";
import { ApiError, copilotChat, CopilotChatResponse } from "@/lib/api";

/**
 * Global, fixed bottom command bar rendered once in the shared dashboard
 * layout so it is present on every authenticated route.
 *
 * It contains:
 *  - the "X Issues" badge (IssueBadge, bottom-left, above the chat input)
 *  - the "HSDEEP CORE AI MODE" status pill (ACTIVE / THINKING)
 *  - the "Ask HSDEEP CORE AI anything…" chat input
 *  - the real AI reply surface (Copilot wiring): honest pending state, a
 *    single time-based "still working" hint after 15s, real error state,
 *    and the latest response with a distinct task-created indicator.
 *
 * Latency contract: POST /api/copilot/chat can legitimately take ~2
 * minutes (local LLM inference). The client deadline is 3 minutes (see
 * copilotChat in lib/api.ts). While a request is in flight the input and
 * submit are disabled so a second overlapping message cannot be fired.
 * No fake progress, no canned replies.
 *
 * A ResizeObserver (plus a MutationObserver so the badge appearing/disappearing
 * is caught) measures the bar's true rendered footprint every time it changes
 * and writes it to the `--copilot-bar-height` CSS variable. The shared
 * scroll container (`.hs-app` / `.main-content-scroll` in globals.css) uses
 * that variable as its `padding-bottom`, which guarantees the last section of
 * every page stays clickable/visible above the fixed bar instead of hiding
 * underneath it.
 */

const SLOW_HINT_AFTER_MS = 15_000;

const CopilotBar = memo(function CopilotBar() {
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [slow, setSlow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reply, setReply] = useState<CopilotChatResponse | null>(null);
  const chatRef = useRef<HTMLFormElement>(null);
  const slowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function clearSlowTimer() {
    if (slowTimerRef.current !== null) {
      clearTimeout(slowTimerRef.current);
      slowTimerRef.current = null;
    }
  }

  useEffect(() => {
    // Cleanup on unmount only.
    return () => clearSlowTimer();
  }, []);

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const text = message.trim();
    if (!text || pending) return;

    setMessage("");
    setError(null);
    setReply(null);
    setPending(true);
    setSlow(false);
    clearSlowTimer();
    slowTimerRef.current = setTimeout(() => setSlow(true), SLOW_HINT_AFTER_MS);

    try {
      const response = await copilotChat(text);
      setReply(response);
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) {
        setError("Couldn't reach the AI service — try again.");
      } else if (err instanceof ApiError) {
        setError(err.message || "The AI service returned an error — try again.");
      } else if (err instanceof DOMException && err.name === "AbortError") {
        setError("The AI service took too long to respond — try again.");
      } else {
        setError("Couldn't reach the AI service — try again.");
      }
    } finally {
      clearSlowTimer();
      setPending(false);
      setSlow(false);
    }
  }

  // Keep `--copilot-bar-height` in sync with the bar's real height.
  // The chat form is always rendered, so it always contributes its footprint;
  // the issue badge only renders when there are active issues, so its (larger)
  // footprint is folded in when present.
  useEffect(() => {
    const chat = chatRef.current;
    if (!chat || typeof window === "undefined") return;

    const root = document.documentElement;

    const measure = () => {
      const chatRect = chat.getBoundingClientRect();
      // Distance from the bar's top edge to the bottom of the viewport.
      let reserved = window.innerHeight - chatRect.top;

      const badge = document.querySelector<HTMLElement>(".issue-badge");
      if (badge) {
        const badgeRect = badge.getBoundingClientRect();
        reserved = Math.max(reserved, window.innerHeight - badgeRect.top);
      }

      root.style.setProperty(
        "--copilot-bar-height",
        `${Math.round(Math.max(0, reserved))}px`
      );
    };

    measure();

    const resizeObserver = new ResizeObserver(measure);
    resizeObserver.observe(chat);
    // Re-measure when the issue badge is mounted/unmounted (it only renders
    // when there are active issues).
    const mutationObserver = new MutationObserver(measure);
    mutationObserver.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", measure);

    return () => {
      resizeObserver.disconnect();
      mutationObserver.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  const hasSurface = pending || error !== null || reply !== null;

  return (
    <>
      {/* Issue badge — bottom-left, above chat bar, not clipped */}
      <IssueBadge />

      <form
        ref={chatRef}
        className="command-center__chat"
        onSubmit={sendMessage}
      >
        <div className="command-center__mode">
          <span className="command-center__mode-dot" />
          HSDEEP CORE AI MODE: <b>{pending ? "THINKING" : "ACTIVE"}</b>
        </div>

        {hasSurface && (
          <div
            className="command-center__reply"
            style={{
              maxHeight: 180,
              overflowY: "auto",
              padding: "8px 12px",
              margin: "6px 0 0",
              border: "1px solid var(--line)",
              background: "rgba(1,10,17,0.85)",
              fontSize: 13,
              lineHeight: 1.5,
              color: "#a7bec7",
            }}
          >
            {pending && (
              <p style={{ margin: 0, color: "var(--cyan)" }}>
                Thinking…
                {slow && (
                  <span style={{ color: "var(--muted)" }}>
                    {" "}still working — this can take a couple of minutes
                  </span>
                )}
              </p>
            )}
            {!pending && error !== null && (
              <p style={{ margin: 0, color: "#ff7b7b" }}>{error}</p>
            )}
            {!pending && error === null && reply !== null && (
              <>
                {reply.task_created && (
                  <p
                    style={{
                      margin: "0 0 6px",
                      display: "inline-block",
                      padding: "2px 8px",
                      border: "1px solid rgba(0,217,255,0.5)",
                      background: "rgba(0,95,128,0.18)",
                      color: "var(--cyan)",
                      fontSize: 12,
                    }}
                  >
                    TASK CREATED {reply.task_number ?? ""}
                    {reply.department ? ` · ${reply.department}` : ""}
                    {reply.agent ? ` · ${reply.agent}` : ""}
                  </p>
                )}
                <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{reply.answer}</p>
              </>
            )}
          </div>
        )}

        <div className="command-center__input">
          <Bot size={20} />
          <input
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            disabled={pending}
            placeholder="Ask HSDEEP CORE AI anything…"
            aria-label="Message HSDEEP Core AI"
          />
          <button type="button" aria-label="Voice input" disabled={pending}><Mic size={18} /></button>
          <button type="submit" aria-label="Send message" disabled={pending || !message.trim()}>
            <Send size={18} />
          </button>
        </div>
      </form>
    </>
  );
});

export default CopilotBar;
