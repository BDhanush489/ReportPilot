import { Container } from "@/components/marketing/ui/Container";
import { CONTACT_HREF } from "@/lib/marketing/links";

const COLUMNS = [
  {
    heading: "Product",
    links: [
      { label: "How it's verified", href: "#trust" },
      { label: "How it works", href: "#how-it-works" },
      { label: "Deliverables", href: "#deliverables" },
      { label: "Pricing", href: "#pricing" },
    ],
  },
  {
    heading: "Company",
    links: [
      { label: "About", href: "/about" },
      { label: "Contact", href: CONTACT_HREF },
    ],
  },
  {
    heading: "Legal",
    links: [
      { label: "Privacy", href: "/privacy" },
      { label: "Terms", href: "/terms" },
    ],
  },
];

export function Footer() {
  return (
    <footer className="border-t border-mkt-ink-line bg-mkt-ink py-16">
      <Container>
        <div className="grid gap-12 md:grid-cols-[1.4fr_1fr_1fr_1fr]">
          <div>
            <span className="font-mkt-display text-lg italic text-canvas">ReportPilot</span>
            <p className="mt-3 max-w-xs text-sm leading-relaxed text-on-mkt-ink-muted">
              Client-ready reports and dashboards, every number machine-verified
              before it ships.
            </p>
          </div>

          {COLUMNS.map((col) => (
            <div key={col.heading}>
              <h4 className="text-xs font-medium uppercase tracking-[0.16em] text-on-mkt-ink-muted">
                {col.heading}
              </h4>
              <ul className="mt-4 flex flex-col gap-3">
                {col.links.map((link) => (
                  <li key={link.label}>
                    <a
                      href={link.href}
                      className="text-sm text-on-mkt-ink-muted transition-colors duration-300 hover:text-canvas"
                    >
                      {link.label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="mt-14 flex flex-col gap-4 border-t border-mkt-ink-line pt-8 text-xs text-on-mkt-ink-muted sm:flex-row sm:items-center sm:justify-between">
          <span>© {new Date().getFullYear()} ReportPilot. All rights reserved.</span>
          <span>Built on a pipeline that never guesses a number.</span>
        </div>
      </Container>
    </footer>
  );
}
