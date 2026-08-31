import { useEffect, useRef } from 'react';
import { LogoMark } from './Logo';

function useReveal() {
  const rootRef = useRef(null);
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const nodes = Array.from(root.querySelectorAll('[data-reveal]'));
    const show = (el) => el.classList.add('is-revealed');
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            show(entry.target);
            observer.unobserve(entry.target);
          }
        }
      },
      { rootMargin: '0px 0px -12% 0px', threshold: 0.08 }
    );
    nodes.forEach((el) => observer.observe(el));
    const fallback = setTimeout(() => nodes.forEach(show), 2600);
    return () => {
      observer.disconnect();
      clearTimeout(fallback);
    };
  }, []);
  return rootRef;
}

function HeroBackground() {
  return (
    <div className="hero-bg-wrap">
      <svg className="hero-bg" viewBox="0 0 1200 760" preserveAspectRatio="xMidYMid slice">
        <g className="drift1" stroke="var(--border-strong)" fill="none" strokeWidth="1" opacity="0.5">
          <path d="M760 90 L860 90 L860 170 L960 170" />
          <path d="M860 170 L860 250 L980 250" />
          <path d="M760 90 L760 330 L880 330" />
          <path d="M880 330 L880 410 L1010 410" />
          <path d="M760 330 L760 500 L900 500" />
        </g>
        <g className="drift2" stroke="var(--border)" fill="none" strokeWidth="1">
          <path d="M120 560 L240 560 L240 470 L360 470" />
          <path d="M240 560 L240 650 L390 650" />
          <path d="M120 560 L120 300 L260 300" />
          <path d="M260 300 L260 210 L400 210" />
        </g>
        <g className="drift1" fill="var(--accent)">
          <circle cx="960" cy="170" r="3" className="pulse-node" style={{ animationDuration: '7s' }} />
          <circle cx="980" cy="250" r="2.5" className="pulse-node" style={{ animationDuration: '9s', animationDelay: '.8s' }} />
          <circle cx="1010" cy="410" r="3" className="pulse-node" style={{ animationDuration: '8s', animationDelay: '1.6s' }} />
          <circle cx="900" cy="500" r="2.5" className="pulse-node" style={{ animationDuration: '11s', animationDelay: '.4s' }} />
        </g>
        <g className="drift2" fill="var(--text)" opacity="0.35">
          <circle cx="360" cy="470" r="2.5" className="pulse-node" style={{ animationDuration: '10s' }} />
          <circle cx="390" cy="650" r="2" className="pulse-node" style={{ animationDuration: '12s', animationDelay: '2s' }} />
          <circle cx="400" cy="210" r="2.5" className="pulse-node" style={{ animationDuration: '8.5s', animationDelay: '1s' }} />
        </g>
        <g className="drift1" stroke="var(--accent)" fill="none" strokeWidth="1.4" strokeDasharray="600" opacity="0.9">
          <path d="M760 90 L860 90 L860 170 L960 170" className="traverse-path" style={{ animationDuration: '9s' }} />
          <path d="M760 330 L880 330 L880 410 L1010 410" className="traverse-path" style={{ animationDuration: '11s', animationDelay: '3s' }} />
        </g>
        <g className="drift3" stroke="var(--text-faint)" fill="none" strokeWidth="1" opacity="0.35">
          <path d="M520 60 L520 720" strokeDasharray="2 10" />
          <path d="M660 0 L660 760" strokeDasharray="2 14" />
        </g>
      </svg>
    </div>
  );
}

export default function LandingPage() {
  const rootRef = useReveal();

  return (
    <div className="landing" ref={rootRef}>
      <header className="landing-header">
        <div className="landing-logo">
          <LogoMark />
          <span className="logo-word">SLEUTH</span>
        </div>
        <nav className="landing-nav">
          <div className="nav-links">
            <a href="#problem">Why it's hard</a>
            <a href="#features">Features</a>
            <a href="#how">How it works</a>
            <a href="#preview">Product</a>
          </div>
          <a className="nav-cta" href="/login">Connect a repo</a>
        </nav>
      </header>

      <section className="hero">
        <HeroBackground />
        <div className="hero-content" data-reveal>
          <div className="eyebrow">
            <span className="eyebrow-rule" />Code intelligence, grounded
          </div>
          <h1>Understand any codebase without reading every file.</h1>
          <p className="hero-sub">
            Sleuth chats with your repository the way a senior engineer would: parsing
            structure, chasing references, opening the files that matter. Every answer
            comes back with the lines it came from.
          </p>
          <div className="hero-ctas">
            <a className="btn-primary hero-btn" href="/login">
              Connect a repo<span className="btn-arrow">&#8594;</span>
            </a>
            <a className="btn-secondary hero-btn" href="#how">
              See how it works
            </a>
          </div>
          <div className="hero-tags">
            <span>AST-LEVEL INDEXING</span>
            <span>TOOL-LOOP RETRIEVAL</span>
            <span>FILE:LINE CITATIONS</span>
            <span>REPO-SCOPED CONTEXT</span>
          </div>
        </div>
      </section>

      <section id="problem" className="section-band alt">
        <div className="section-inner problem-grid" data-reveal>
          <div className="problem-lede">
            <div className="section-eyebrow">01 / THE PROBLEM</div>
            <h2>Text chunking breaks code.</h2>
            <p>
              Generic RAG treats a repository as prose. It slices files into fixed
              windows, embeds them, and hopes the right window comes back. Code doesn't
              survive that.
            </p>
          </div>
          <div className="problem-rows">
            <div className="problem-row">
              <span className="num">01</span>
              <div>
                <h4>Functions get cut mid-body</h4>
                <p>A 400-token window ends halfway through a handler. The retrieved
                  chunk has a signature with no logic, or logic with no signature.</p>
              </div>
            </div>
            <div className="problem-row">
              <span className="num">02</span>
              <div>
                <h4>Call relationships disappear</h4>
                <p>Embeddings capture lexical similarity, not the edges of a call
                  graph. The definition and its three call sites live in four
                  unrelated vectors.</p>
              </div>
            </div>
            <div className="problem-row">
              <span className="num">03</span>
              <div>
                <h4>"What calls this?" is unanswerable</h4>
                <p>One-shot similarity search can't follow a thread. It returns the
                  nearest paragraph and the model fills the rest in with plausible
                  fiction.</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="features" className="section-band">
        <div className="section-inner" data-reveal style={{ paddingBottom: 'clamp(20px,3vw,40px)' }}>
          <div className="section-eyebrow">02 / WHAT SLEUTH DOES</div>
          <h2 style={{ maxWidth: '18ch', marginBottom: 0 }}>Built for the shape of a repository.</h2>
        </div>
        <div className="section-inner feature-cards" data-reveal style={{ paddingTop: 0 }}>
          <div className="feature-card">
            <div className="index">a.</div>
            <h3>Structure-aware chunking</h3>
            <p>Sleuth parses each file to an AST and indexes at function, method and
              class boundaries. A unit of retrieval is a unit of code, with its
              signature, docstring, imports and enclosing scope intact.</p>
            <pre className="feature-detail">{`class SessionStore → 1 node
  .rotate() → 1 node
  .revoke() → 1 node`}</pre>
          </div>
          <div className="feature-card">
            <div className="index">b.</div>
            <h3>Agentic retrieval</h3>
            <p>Not one embedding lookup. Sleuth runs a tool loop — grep for a symbol,
              read the file around it, follow the import, read again — until it has
              the evidence to answer, or reports that it doesn't.</p>
            <pre className="feature-detail">{`› grep "refreshToken"
› read_file auth/session.ts
› read_file api/middleware.ts`}</pre>
          </div>
          <div className="feature-card">
            <div className="index">c.</div>
            <h3>Repo-scoped &amp; cited</h3>
            <p>Context never leaves the repository you connected. Every claim carries
              a file path and line range you can check, so review takes seconds and
              hallucination has nowhere to hide.</p>
            <pre className="feature-detail">{`src/auth/session.ts:142–149
src/api/middleware.ts:57–73
scope: acme/platform`}</pre>
          </div>
        </div>
      </section>

      <section id="how" className="section-band alt">
        <div className="section-inner" data-reveal>
          <div className="section-eyebrow">03 / HOW IT WORKS</div>
          <h2 style={{ maxWidth: '16ch' }}>Three steps, then ask anything.</h2>
          <div className="how-steps">
            <div className="how-step">
              <div className="how-num">01</div>
              <h3>Connect a repo</h3>
              <p>Point Sleuth at a GitHub URL. Read-only access, one branch or all of
                them.</p>
            </div>
            <div className="how-step">
              <div className="how-num">02</div>
              <h3>Sleuth indexes structurally</h3>
              <p>Files are parsed, not split. Symbols, definitions and references
                become a graph that stays in sync on every push.</p>
            </div>
            <div className="how-step">
              <div className="how-num">03</div>
              <h3>Ask, get cited answers</h3>
              <p>Ask in plain language. Sleuth investigates, then answers with the
                exact lines it read.</p>
            </div>
          </div>
        </div>
      </section>

      <section id="preview" className="section-band">
        <div className="section-inner" data-reveal>
          <div className="preview-head">
            <h2>Answers you can check in one click.</h2>
            <div className="preview-meta">acme/platform &#183; main</div>
          </div>
          <div className="browser-mock">
            <div className="browser-chrome">
              <div className="chrome-dots">
                <span className="chrome-dot" />
                <span className="chrome-dot" />
                <span className="chrome-dot" />
              </div>
              <div className="chrome-title">sleuth &#8212; acme/platform</div>
            </div>
            <div className="browser-body">
              <div className="chat-row">
                <span className="chat-who">you</span>
                <span className="chat-msg">Where do we invalidate a refresh token, and what calls it?</span>
              </div>
              <div className="chat-row">
                <span className="chat-who answer">slt</span>
                <div className="chat-answer-body">
                  <p>
                    Invalidation happens in <code>SessionStore.revoke()</code>, which
                    deletes the token row and publishes a <code>session.revoked</code>{' '}
                    event. It has two callers: the logout route, and the auth
                    middleware when it detects a reused token.
                  </p>
                  <div className="code-block">
                    <div className="code-block-head">
                      <span>src/auth/session.ts</span>
                      <span>142&#8211;149</span>
                    </div>
                    <pre>{`142  async revoke(id: string) {
143    const row = await this.db.tokens
144      .where({ id }).delete();
145    if (!row) return false;
146    await this.bus.publish(
147      "session.revoked", { id });
148    return true;
149  }`}</pre>
                  </div>
                  <div className="citation-pills">
                    <span className="citation-pill">src/api/routes.ts:88</span>
                    <span className="citation-pill">src/api/middleware.ts:57&#8211;73</span>
                  </div>
                </div>
              </div>
              <div className="chat-input-row">
                <span className="chat-input-arrow">&#8250;</span>
                <span>Ask about acme/platform</span>
                <span className="chat-caret" />
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="cta" className="section-band alt">
        <div className="cta-inner" data-reveal>
          <h2>Stop guessing. Start reading the code.</h2>
          <p>Connect a repository and ask your first question in under two minutes.</p>
          <a className="btn-primary" href="/login">
            Connect a repo<span className="btn-arrow">&#8594;</span>
          </a>
        </div>
      </section>

      <footer className="landing-footer">
        <div className="landing-footer-inner">
          <div className="footer-brand">
            <span className="logo-word">SLEUTH</span>
            <span className="footer-copyright">&#169; {new Date().getFullYear()}</span>
          </div>
          <div className="footer-links">
            <a href="#features">Product</a>
            <a href="#how">Docs</a>
            <a href="#problem">Security</a>
            <a href="#cta">Contact</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
