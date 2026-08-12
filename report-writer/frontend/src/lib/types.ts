export type ReportChart = {
  caption: string;
  img: string;
};

export type ReportSection = {
  heading: string;
  narrative: string;
  recommendations: string[];
  charts?: ReportChart[];
};

export type Report = {
  report_title: string;
  period_label: string;
  executive_summary: string;
  highlights: string[];
  watchouts: string[];
  sections: ReportSection[];
  next_steps: string[];
};

export type QaBadge = {
  badge: "PASS" | "PASS-WITH-WARNINGS" | "FAIL";
  failing_checks: string[];
};

export type GenerateResponse = {
  report_id: string;
  report: Report;
  ai_generated: boolean;
  ai_provider: string | null;
  ai_error: string | null;
  // Absent for a report generated before the canonical report object
  // shipped on the backend -- treat as "no badge to show," not a failure.
  qa?: QaBadge | null;
};

export type RecentReport = {
  report_id: string;
  created_at: string;
  agency_name: string | null;
  client_name: string | null;
  report_title: string | null;
  period_label: string | null;
  ai_generated: boolean;
  ai_provider: string | null;
};
