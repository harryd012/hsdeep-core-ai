import { memo, type ReactNode } from "react";

/**
 * ZoneSection — minimal layout/composition shell for the dashboard zones
 * introduced by P0-4B (Enterprise AIOps Control Plane information architecture).
 *
 * Zones:
 *   01 Operational Health · 02 AI Operations · 03 Infrastructure Operations
 *   04 Analytics + Activity · 05 Footer / System Information
 *
 * This component is PURELY presentational. It owns no state, no hooks, no API
 * calls and no data of its own — it only renders a zone heading (index, title,
 * operator caption) plus an optional right-hand slot for *existing* navigation,
 * and wraps whatever existing dashboard panels the zone composes. That keeps
 * the "compose / move / group / restyle" contract: it can never introduce a
 * second source of truth or duplicate fetching.
 */
interface ZoneSectionProps {
  /** Zone number rendered as a small badge, e.g. "01". */
  index: string;
  /** Zone title, e.g. "OPERATIONAL HEALTH". */
  title: string;
  /** One-line operator intent for the zone (static copy, never data). */
  caption?: string;
  /** Optional right-aligned slot — only for links/labels that already exist. */
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
}

const ZoneSection = memo(function ZoneSection({
  index,
  title,
  caption,
  aside,
  className,
  children,
}: ZoneSectionProps) {
  return (
    <section
      className={className ? `hs-noc__zone ${className}` : "hs-noc__zone"}
      aria-label={title}
    >
      <header className="hs-noc__zone-head">
        <span className="hs-noc__zone-index" aria-hidden="true">
          {index}
        </span>
        <div className="hs-noc__zone-titles">
          <h2>{title}</h2>
          {caption ? <p>{caption}</p> : null}
        </div>
        {aside ? <div className="hs-noc__zone-aside">{aside}</div> : null}
      </header>
      <div className="hs-noc__zone-body">{children}</div>
    </section>
  );
});

export default ZoneSection;
