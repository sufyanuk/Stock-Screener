"use client";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Button, ErrorBox, Field } from "@/components/ui";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`/auth/${mode}`, { method: "POST", body: JSON.stringify({ email, password }) }); // sets session cookie
      router.replace("/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <form onSubmit={submit} className="panel w-full max-w-sm space-y-4 p-6">
        <div>
          <h1 className="text-lg font-semibold">Stock Intel</h1>
          <p className="text-sm text-muted">{mode === "login" ? "Sign in to your research terminal." : "Create an account. The first account becomes the administrator."}</p>
        </div>
        <Field label="Email"><input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" /></Field>
        <Field label="Password" hint={mode === "register" ? "At least 10 characters." : undefined}>
          <input type="password" required minLength={10} value={password} onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"} />
        </Field>
        <ErrorBox error={error} />
        <Button type="submit" disabled={busy} className="w-full">{busy ? "…" : mode === "login" ? "Sign in" : "Create account"}</Button>
        <button type="button" className="w-full text-center text-xs text-muted hover:text-accent" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "No account? Register" : "Have an account? Sign in"}
        </button>
      </form>
    </div>
  );
}
