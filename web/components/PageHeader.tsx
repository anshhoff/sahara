import type { ReactNode } from "react";

export function PageHeader({
  title,
  question,
  children,
}: {
  title: string;
  question?: string;
  children?: ReactNode;
}) {
  return (
    <header className="mb-6">
      <h1 className="text-[22px] font-semibold tracking-tight">{title}</h1>
      {question && (
        <p className="mt-1 max-w-[76ch] text-[13px] leading-relaxed text-[var(--text-2)]">
          {question}
        </p>
      )}
      {children}
    </header>
  );
}
