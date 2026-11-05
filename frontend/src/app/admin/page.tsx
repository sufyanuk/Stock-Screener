import { redirect } from "next/navigation";

// The Admin page was removed: each tab now holds its own criteria.
export default function AdminRedirect() {
  redirect("/dashboard");
}
