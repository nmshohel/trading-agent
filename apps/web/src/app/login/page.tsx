"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useRouter } from "next/navigation";

const LoginFormSchema = z.object({
  email: z.string().email("Enter a valid email address"),
  password: z.string().min(8, "Password must be at least 8 characters"),
});

type LoginFormValues = z.infer<typeof LoginFormSchema>;

export default function LoginPage() {
  const router = useRouter();
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormValues>({
    resolver: zodResolver(LoginFormSchema),
  });

  const onSubmit = async (values: LoginFormValues) => {
    setServerError(null);
    const res = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(values),
    });

    if (!res.ok) {
      const data = (await res.json().catch(() => ({}))) as { error?: string };
      setServerError(data.error ?? "Login failed");
      return;
    }

    router.push("/");
    router.refresh();
  };

  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-md rounded-xl border border-slate-800 bg-slate-900 p-6 space-y-5">
        <div className="space-y-1">
          <h1 className="text-xl font-bold">Owner Sign In</h1>
          <p className="text-xs text-slate-400">
            Private access — Exness MT5 Semi-Automatic Trading Agent
          </p>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="space-y-1">
            <label className="text-sm text-slate-300" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              {...register("email")}
              className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm focus:border-amber-500 focus:outline-none"
              placeholder="owner@local.dev"
            />
            {errors.email && (
              <p className="text-xs text-rose-400">{errors.email.message}</p>
            )}
          </div>

          <div className="space-y-1">
            <label className="text-sm text-slate-300" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              {...register("password")}
              className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm focus:border-amber-500 focus:outline-none"
              placeholder="••••••••••••"
            />
            {errors.password && (
              <p className="text-xs text-rose-400">{errors.password.message}</p>
            )}
          </div>

          {serverError && (
            <p className="rounded bg-rose-500/10 border border-rose-500/30 px-3 py-2 text-xs text-rose-300">
              {serverError}
            </p>
          )}

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full rounded-md bg-amber-500 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-400 disabled:opacity-50 transition"
          >
            {isSubmitting ? "Signing in..." : "Sign In"}
          </button>
        </form>
      </div>
    </main>
  );
}
