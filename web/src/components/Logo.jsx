// Shared brand mark — same three bars (top rule / vertical / horizontal)
// inside a rounded square used on the landing page footer/header. Extracted
// here so NavRail (Task: sidebar branding) can render the identical mark
// instead of a second hand-copied version drifting out of sync with it.
export function LogoMark({ className = '' }) {
  return (
    <div className={`logo-mark ${className}`.trim()} aria-hidden="true">
      <span className="bar-top" />
      <span className="bar-v" />
      <span className="bar-h" />
    </div>
  );
}

// Animated mascot used as the "the assistant is working" indicator (chat
// thinking state) — same mark as the brand logo, so the working indicator
// reads as "Sleuth itself" rather than a generic spinner, the way Claude's
// own animated star mark does for its thinking state. See .logo-mark-pulse
// in theme.css for the actual animation.
export function LogoMarkPulse() {
  return <LogoMark className="logo-mark-pulse" />;
}

export default function Logo({ wordClassName = 'logo-word' }) {
  return (
    <>
      <LogoMark />
      <span className={wordClassName}>SLEUTH</span>
    </>
  );
}
