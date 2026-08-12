import { redirect } from "next/navigation";

// The marketing site lives in a separate project (../marketing-site) --
// this app's own root is just the tool's entry point. /app itself handles
// the real auth check (redirects to /login when there's no session), so
// this redirect can stay unconditional.
export default function RootPage() {
  redirect("/app");
}
