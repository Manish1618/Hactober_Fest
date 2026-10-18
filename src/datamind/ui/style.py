"""Look and motion for the interface: CSS only, so nothing here slows an answer down."""
# ruff: noqa: E501

from __future__ import annotations

import html

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
  --dm-violet: #8b5cf6;
  --dm-cyan: #22d3ee;
  --dm-pink: #ec4899;
  --dm-glass: rgba(255, 255, 255, 0.045);
  --dm-line: rgba(255, 255, 255, 0.09);
}

html, body, [class*="css"], .stMarkdown, button, input, textarea {
  font-family: 'Inter', system-ui, sans-serif !important;
}

/* Slow aurora behind everything */
.stApp {
  background:
    radial-gradient(60rem 40rem at 10% -10%, rgba(139, 92, 246, 0.22), transparent 60%),
    radial-gradient(50rem 35rem at 110% 10%, rgba(34, 211, 238, 0.14), transparent 60%),
    radial-gradient(45rem 30rem at 50% 120%, rgba(236, 72, 153, 0.12), transparent 60%),
    #07070d;
  background-size: 140% 140%;
  animation: dm-aurora 22s ease-in-out infinite alternate;
}
@keyframes dm-aurora {
  0% { background-position: 0% 0%, 100% 0%, 50% 100%, 0 0; }
  100% { background-position: 20% 10%, 80% 15%, 40% 85%, 0 0; }
}

/* Messages rise in */
[data-testid="stChatMessage"] {
  background: var(--dm-glass);
  border: 1px solid var(--dm-line);
  border-radius: 18px;
  padding: 1rem 1.2rem;
  backdrop-filter: blur(14px);
  animation: dm-rise 0.55s cubic-bezier(.2, .8, .2, 1) both;
}
@keyframes dm-rise {
  from { opacity: 0; transform: translateY(14px) scale(.985); filter: blur(4px); }
  to { opacity: 1; transform: none; filter: none; }
}

/* Buttons: pills with a glow on hover */
.stButton > button, .stDownloadButton > button {
  border-radius: 999px;
  border: 1px solid var(--dm-line);
  background: rgba(255, 255, 255, 0.04);
  transition: transform .18s ease, box-shadow .25s ease, border-color .25s ease, background .25s ease;
}
.stButton > button:hover, .stDownloadButton > button:hover {
  transform: translateY(-2px);
  border-color: rgba(139, 92, 246, .7);
  background: rgba(139, 92, 246, .14);
  box-shadow: 0 8px 28px -10px rgba(139, 92, 246, .8);
}

/* Evidence and status panels */
[data-testid="stExpander"] details {
  border-radius: 14px;
  border: 1px solid var(--dm-line);
  background: rgba(255, 255, 255, 0.025);
}
[data-testid="stChatInput"] {
  border-radius: 16px;
  box-shadow: 0 0 0 1px rgba(139, 92, 246, .35), 0 12px 40px -18px rgba(139, 92, 246, .9);
}
[data-testid="stSidebar"] {
  background: rgba(10, 10, 20, .72);
  border-right: 1px solid var(--dm-line);
  backdrop-filter: blur(18px);
}

/* Hero */
.dm-hero { text-align: center; padding: 4.5rem 1rem 2.5rem; animation: dm-rise .8s both; }
.dm-pill {
  display: inline-block; padding: .35rem .9rem; border-radius: 999px; font-size: .78rem;
  letter-spacing: .04em; color: #c4b5fd; border: 1px solid rgba(139, 92, 246, .45);
  background: rgba(139, 92, 246, .12);
}
.dm-title {
  font-size: clamp(2.4rem, 6vw, 4.2rem); font-weight: 800; line-height: 1.05;
  margin: 1.1rem 0 .8rem; letter-spacing: -.03em;
  background: linear-gradient(90deg, #fff, #c4b5fd, var(--dm-cyan), #f0abfc, #fff);
  background-size: 300% 100%;
  -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: dm-shine 8s linear infinite;
}
@keyframes dm-shine { to { background-position: 300% 0; } }
.dm-sub { color: #a1a1b5; font-size: 1.05rem; max-width: 40rem; margin: 0 auto; }
.dm-steps { display: flex; gap: .6rem; justify-content: center; flex-wrap: wrap; margin-top: 1.6rem; }
.dm-step {
  padding: .45rem .8rem; border-radius: 12px; font-size: .82rem; color: #d4d4e0;
  border: 1px solid var(--dm-line); background: var(--dm-glass);
  animation: dm-rise .6s both;
}
.dm-step:nth-child(2) { animation-delay: .08s; } .dm-step:nth-child(3) { animation-delay: .16s; }
.dm-step:nth-child(4) { animation-delay: .24s; } .dm-step:nth-child(5) { animation-delay: .32s; }

/* KPI card */
.dm-kpi {
  position: relative; overflow: hidden; border-radius: 18px; padding: 1.2rem 1.4rem;
  margin: .2rem 0 .6rem; border: 1px solid rgba(139, 92, 246, .35);
  background: linear-gradient(135deg, rgba(139, 92, 246, .18), rgba(34, 211, 238, .08));
  animation: dm-pop .6s cubic-bezier(.2, .9, .3, 1.2) both;
}
.dm-kpi::after {
  content: ""; position: absolute; inset: 0;
  background: linear-gradient(110deg, transparent 30%, rgba(255, 255, 255, .12) 50%, transparent 70%);
  transform: translateX(-100%); animation: dm-sweep 2.4s ease .4s 1 forwards;
}
@keyframes dm-pop { from { opacity: 0; transform: scale(.94); } to { opacity: 1; transform: none; } }
@keyframes dm-sweep { to { transform: translateX(100%); } }
.dm-kpi-label { text-transform: uppercase; letter-spacing: .08em; font-size: .75rem; color: #c4b5fd; }
.dm-kpi-value { font-size: clamp(1.8rem, 4vw, 2.8rem); font-weight: 800; letter-spacing: -.02em; }
.dm-kpi-answer { color: #b9b9c9; font-size: .95rem; margin-top: .2rem; }
.dm-label {
  margin: .8rem 0 .2rem; font-size: .75rem; letter-spacing: .08em; text-transform: uppercase;
  color: #8b8ba3;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
</style>
"""

STEPS = ("Understand", "Write SQL", "Check it", "Run on Snowflake", "Verify every number")


def hero(dataset: str) -> str:
    steps = "".join(f'<span class="dm-step">{s}</span>' for s in STEPS)
    return (
        '<div class="dm-hero">'
        f'<span class="dm-pill">Gemma · Snowflake · {html.escape(dataset)}</span>'
        '<div class="dm-title">Ask your data anything.</div>'
        '<p class="dm-sub">Plain-English questions become checked SQL, run on Snowflake, with '
        "every number traced back to the query that produced it.</p>"
        f'<div class="dm-steps">{steps}</div></div>'
    )
