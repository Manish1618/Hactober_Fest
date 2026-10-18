"""Look and motion for the interface: CSS only, so nothing here slows an answer down."""
# ruff: noqa: E501

from __future__ import annotations

import html

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500&display=swap');

@property --dm-n { syntax: '<integer>'; initial-value: 0; inherits: false; }
@property --dm-angle { syntax: '<angle>'; initial-value: 0deg; inherits: false; }

:root {
  --dm-violet: #8b5cf6;
  --dm-cyan: #22d3ee;
  --dm-pink: #ec4899;
  --dm-lime: #a3e635;
  --dm-glass: rgba(255, 255, 255, 0.045);
  --dm-line: rgba(255, 255, 255, 0.09);
  --dm-ease: cubic-bezier(.2, .8, .2, 1);
}

html, body, [class*="css"], .stMarkdown, button, input, textarea {
  font-family: 'Inter', system-ui, sans-serif !important;
}

/* ---- Living background: drifting orbs over a slowly panning grid ---- */
.stApp { background: #06060c; }
[data-testid="stAppViewContainer"]::before, [data-testid="stAppViewContainer"]::after {
  content: ""; position: fixed; z-index: -1; pointer-events: none; border-radius: 50%;
  filter: blur(90px); opacity: .55; mix-blend-mode: screen;
}
[data-testid="stAppViewContainer"]::before {
  width: 42rem; height: 42rem; left: -10rem; top: -14rem;
  background: radial-gradient(circle, var(--dm-violet), transparent 65%);
  animation: dm-orb-a 18s ease-in-out infinite alternate;
}
[data-testid="stAppViewContainer"]::after {
  width: 36rem; height: 36rem; right: -12rem; top: 20%;
  background: radial-gradient(circle, var(--dm-cyan), transparent 65%);
  animation: dm-orb-b 22s ease-in-out infinite alternate;
}
@keyframes dm-orb-a { to { transform: translate(28vw, 30vh) scale(1.25); } }
@keyframes dm-orb-b { to { transform: translate(-30vw, 28vh) scale(.85); } }
[data-testid="stMain"] { isolation: isolate; }
[data-testid="stMain"]::before {
  content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none;
  background-image:
    linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);
  background-size: 56px 56px;
  animation: dm-grid 40s linear infinite;
  -webkit-mask-image: radial-gradient(ellipse at 50% 20%, #000 30%, transparent 80%);
          mask-image: radial-gradient(ellipse at 50% 20%, #000 30%, transparent 80%);
}
[data-testid="stAppViewContainer"] { isolation: isolate; }
@keyframes dm-grid { to { background-position: 56px 56px, 56px 56px; } }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stBottom"] > div, [data-testid="stBottomBlockContainer"] { background: transparent; }

/* ---- Hero ---- */
.dm-hero { text-align: center; padding: 3.5rem 1rem 1.5rem; }
.dm-pill {
  display: inline-flex; align-items: center; gap: .5rem; padding: .4rem 1rem; border-radius: 999px;
  font-size: .8rem; letter-spacing: .03em; color: #ddd6fe;
  border: 1px solid rgba(139, 92, 246, .45); background: rgba(139, 92, 246, .12);
  animation: dm-rise .7s var(--dm-ease) both;
}
.dm-dot { width: .5rem; height: .5rem; border-radius: 50%; background: var(--dm-lime);
  box-shadow: 0 0 0 0 rgba(163, 230, 53, .7); animation: dm-ping 1.8s infinite; }
@keyframes dm-ping { 70% { box-shadow: 0 0 0 .55rem rgba(163, 230, 53, 0); } 100% { box-shadow: 0 0 0 0 rgba(163, 230, 53, 0); } }
.dm-title {
  font-size: clamp(2.6rem, 6.5vw, 4.8rem); font-weight: 800; line-height: 1.02;
  letter-spacing: -.045em; margin: 1.3rem auto .9rem; max-width: 52rem;
}
.dm-word {
  display: inline-block; margin: 0 .14em; opacity: 0;
  animation: dm-word .9s var(--dm-ease) forwards;
}
.dm-word.dm-grad {
  background: linear-gradient(90deg, var(--dm-violet), var(--dm-cyan), var(--dm-pink), var(--dm-violet));
  background-size: 300% 100%; -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: dm-word .9s var(--dm-ease) forwards, dm-shine 6s linear infinite;
}
@keyframes dm-word { from { opacity: 0; transform: translateY(.6em) rotate(4deg); filter: blur(10px); }
                     to { opacity: 1; transform: none; filter: none; } }
@keyframes dm-shine { to { background-position: 300% 0; } }
.dm-sub { color: #a8a8bd; font-size: 1.08rem; max-width: 40rem; margin: 0 auto;
  animation: dm-rise .9s var(--dm-ease) .7s both; }

.dm-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: 1rem;
  max-width: 58rem; margin: 2.4rem auto 0; text-align: left; }
.dm-card {
  position: relative; padding: 1.15rem 1.2rem; border-radius: 18px; background: rgba(14, 14, 26, .7);
  border: 1px solid var(--dm-line); backdrop-filter: blur(12px);
  animation: dm-rise .8s var(--dm-ease) both;
  transition: transform .35s var(--dm-ease), box-shadow .35s var(--dm-ease);
}
.dm-card::before {
  content: ""; position: absolute; inset: -1px; border-radius: inherit; padding: 1px; opacity: 0;
  background: conic-gradient(from var(--dm-angle), var(--dm-violet), var(--dm-cyan), var(--dm-pink), var(--dm-violet));
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude;
  transition: opacity .35s; animation: dm-spin 4s linear infinite;
}
.dm-card:hover { transform: translateY(-6px) scale(1.015); box-shadow: 0 24px 50px -24px rgba(139, 92, 246, .8); }
.dm-card:hover::before { opacity: 1; }
@keyframes dm-spin { to { --dm-angle: 360deg; } }
.dm-card-icon { font-size: 1.35rem; }
.dm-card-title { font-weight: 700; margin: .45rem 0 .2rem; color: #f4f4fb; }
.dm-card-text { color: #9d9db3; font-size: .88rem; line-height: 1.45; }

/* ---- Pipeline marquee ---- */
.dm-marquee { overflow: hidden; margin: 2rem auto 0; max-width: 58rem;
  -webkit-mask-image: linear-gradient(90deg, transparent, #000 15%, #000 85%, transparent);
          mask-image: linear-gradient(90deg, transparent, #000 15%, #000 85%, transparent); }
.dm-track { display: flex; gap: .7rem; width: max-content; animation: dm-scroll 28s linear infinite; }
.dm-step { padding: .45rem .9rem; border-radius: 999px; font-size: .8rem; color: #d4d4e0; white-space: nowrap;
  border: 1px solid var(--dm-line); background: var(--dm-glass); font-family: 'JetBrains Mono', monospace !important; }
@keyframes dm-scroll { to { transform: translateX(-50%); } }

/* ---- Conversation ---- */
@keyframes dm-rise {
  from { opacity: 0; transform: translateY(18px) scale(.985); filter: blur(6px); }
  to { opacity: 1; transform: none; filter: none; }
}
[data-testid="stChatMessage"] {
  background: rgba(14, 14, 26, .62); border: 1px solid var(--dm-line); border-radius: 20px;
  padding: 1rem 1.2rem; backdrop-filter: blur(16px);
  animation: dm-rise .6s var(--dm-ease) both;
  transition: border-color .3s, box-shadow .3s;
}
[data-testid="stChatMessage"]:hover { border-color: rgba(139, 92, 246, .35); box-shadow: 0 18px 40px -28px rgba(139, 92, 246, .9); }
[data-testid="stStatusWidget"], [data-testid="stExpander"] details {
  border-radius: 14px; border: 1px solid var(--dm-line); background: rgba(255, 255, 255, .025);
}
/* the thinking line shimmers while a question runs */
[data-testid="stExpander"] details:has([data-testid="stExpanderIconSpinner"]) summary p {
  background: linear-gradient(90deg, #8b8ba3 30%, #fff 50%, #8b8ba3 70%); background-size: 200% 100%;
  -webkit-background-clip: text; background-clip: text; color: transparent; animation: dm-shimmer 2.2s linear infinite;
}
@keyframes dm-shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }

/* Charts wipe in from the left */
[data-testid="stPlotlyChart"] { animation: dm-wipe 1.1s var(--dm-ease) .15s both; }
@keyframes dm-wipe { from { clip-path: inset(0 100% 0 0); opacity: .3; } to { clip-path: inset(0 0 0 0); opacity: 1; } }

/* Pills: float gently, lift on hover */
.stButton > button, .stDownloadButton > button {
  border-radius: 999px; border: 1px solid var(--dm-line); background: rgba(255, 255, 255, 0.04);
  transition: transform .25s var(--dm-ease), box-shadow .3s, border-color .3s, background .3s;
}
.stButton > button:hover, .stDownloadButton > button:hover {
  transform: translateY(-3px); border-color: rgba(139, 92, 246, .75); background: rgba(139, 92, 246, .16);
  box-shadow: 0 10px 30px -10px rgba(139, 92, 246, .85);
}
[data-testid="stSidebar"] .stButton, .dm-next ~ div .stButton, [data-testid="stChatMessage"] .stButton {
  animation: dm-float 5s ease-in-out infinite;
}
[data-testid="stSidebar"] .stButton:nth-of-type(2n), [data-testid="stChatMessage"] [data-testid="stColumn"]:nth-child(2n) .stButton { animation-delay: -1.6s; }
[data-testid="stSidebar"] .stButton:nth-of-type(3n), [data-testid="stChatMessage"] [data-testid="stColumn"]:nth-child(3n) .stButton { animation-delay: -3.1s; }
@keyframes dm-float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-3px); } }

[data-testid="stChatInput"] {
  border-radius: 18px;
  box-shadow: 0 0 0 1px rgba(139, 92, 246, .4), 0 14px 44px -18px rgba(139, 92, 246, .95);
  animation: dm-glow 4s ease-in-out infinite;
}
@keyframes dm-glow { 50% { box-shadow: 0 0 0 1px rgba(34, 211, 238, .45), 0 14px 44px -18px rgba(34, 211, 238, .9); } }
[data-testid="stSidebar"] {
  background: rgba(8, 8, 16, .72); border-right: 1px solid var(--dm-line); backdrop-filter: blur(18px);
}

/* ---- KPI card with a count-up ---- */
.dm-kpi {
  position: relative; overflow: hidden; border-radius: 20px; padding: 1.3rem 1.5rem; margin: .2rem 0 .7rem;
  border: 1px solid rgba(139, 92, 246, .4);
  background: radial-gradient(30rem 12rem at 0% 0%, rgba(139, 92, 246, .28), transparent),
              radial-gradient(24rem 10rem at 100% 100%, rgba(34, 211, 238, .16), transparent), rgba(12, 12, 22, .8);
  animation: dm-pop .7s cubic-bezier(.2, .9, .3, 1.25) both;
}
.dm-kpi::after {
  content: ""; position: absolute; inset: 0;
  background: linear-gradient(110deg, transparent 30%, rgba(255, 255, 255, .14) 50%, transparent 70%);
  transform: translateX(-100%); animation: dm-sweep 1.6s ease 1.2s forwards;
}
@keyframes dm-pop { from { opacity: 0; transform: scale(.93) translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes dm-sweep { to { transform: translateX(100%); } }
.dm-kpi-label { text-transform: uppercase; letter-spacing: .1em; font-size: .75rem; color: #c4b5fd; }
.dm-kpi-value { position: relative; font-size: clamp(2rem, 4.5vw, 3.2rem); font-weight: 800; letter-spacing: -.03em;
  font-variant-numeric: tabular-nums; min-height: 1.2em; }
.dm-count { position: absolute; left: 0; top: 0; counter-reset: dm var(--dm-n);
  animation: dm-count 1.2s cubic-bezier(.15, .85, .25, 1) forwards, dm-out .08s 1.2s forwards; }
.dm-count::after { content: counter(dm); }
@keyframes dm-count { from { --dm-n: 0; } to { --dm-n: var(--dm-target); } }
@keyframes dm-out { to { opacity: 0; } }
.dm-final { opacity: 0; animation: dm-in .25s 1.2s forwards; }
.dm-final.dm-now { opacity: 1; animation: none; }
@keyframes dm-in { to { opacity: 1; } }
.dm-kpi-answer { color: #b9b9c9; font-size: .95rem; margin-top: .25rem; }
.dm-label { margin: .9rem 0 .25rem; font-size: .75rem; letter-spacing: .1em; text-transform: uppercase; color: #8b8ba3; }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
  .dm-word, .dm-final { opacity: 1 !important; }
  .dm-count { display: none; }
}
</style>
"""

STEPS = (
    "1 · understand the question",
    "2 · draft SQL with Gemma",
    "3 · validate: read-only, known tables",
    "4 · run on Snowflake",
    "5 · repair if it fails",
    "6 · read the facts from the SQL",
    "7 · verify every number",
    "8 · show the evidence",
)
CARDS = (
    ("✦", "Plain English in", "Ask like you would ask an analyst. Follow-ups keep the context."),
    ("❄", "Real Snowflake runs", "Every answer carries the query ID you can open in Snowsight."),
    (
        "✓",
        "Numbers you can trust",
        "Code checks that each number in the answer came from the query.",
    ),
)


def hero(dataset: str) -> str:
    words = "Ask your data anything.".split()
    title = "".join(
        f'<span class="dm-word{" dm-grad" if w.startswith("data") else ""}" '
        f'style="animation-delay:{0.15 + i * 0.12:.2f}s">{w}</span>'
        for i, w in enumerate(words)
    )
    cards = "".join(
        f'<div class="dm-card" style="animation-delay:{0.9 + i * 0.12:.2f}s">'
        f'<div class="dm-card-icon">{icon}</div><div class="dm-card-title">{t}</div>'
        f'<div class="dm-card-text">{d}</div></div>'
        for i, (icon, t, d) in enumerate(CARDS)
    )
    steps = "".join(f'<span class="dm-step">{s}</span>' for s in STEPS * 2)
    return (
        '<div class="dm-hero">'
        f'<span class="dm-pill"><span class="dm-dot"></span>Gemma · Snowflake · {html.escape(dataset)}</span>'
        f'<div class="dm-title">{title}</div>'
        '<p class="dm-sub">Plain-English questions become checked SQL, run on Snowflake, with '
        "every number traced back to the query that produced it.</p>"
        f'<div class="dm-cards">{cards}</div>'
        f'<div class="dm-marquee"><div class="dm-track">{steps}</div></div></div>'
    )


def kpi_html(value, label: str, answer: str) -> str:
    """A KPI card whose whole-number part counts up, then settles on the exact value."""
    shown = f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"
    target = int(abs(value)) if isinstance(value, int | float) else 0
    count = (
        f'<span class="dm-count" style="--dm-target:{target}"></span>'
        if 0 < target < 2_000_000_000 and value >= 0
        else ""
    )
    final = "dm-final" if count else "dm-final dm-now"
    return (
        f'<div class="dm-kpi"><div class="dm-kpi-label">{html.escape(label)}</div>'
        f'<div class="dm-kpi-value">{count}<span class="{final}">{html.escape(shown)}</span></div>'
        f'<div class="dm-kpi-answer">{html.escape(answer)}</div></div>'
    )
