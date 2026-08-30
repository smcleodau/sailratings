import React from "react";

export function AdminCard({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`sr-card ${className}`}>
      {children}
    </div>
  );
}

export function AdminButton({
  children,
  onClick,
  disabled,
  variant = "primary",
  className = "",
  type = "button"
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary" | "outline";
  className?: string;
  type?: "button" | "submit" | "reset";
}) {
  const variants = {
    primary: "sr-button--primary",
    secondary: "sr-button--secondary",
    outline: "sr-button--ghost"
  };

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`sr-button ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

export function AdminInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`sr-input ${props.className || ""}`}
    />
  );
}

export function AdminLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="sr-field__label mb-1 block">
      {children}
    </label>
  );
}

export function AdminAlert({ children, type = "info" }: { children: React.ReactNode; type?: "info" | "warning" | "error" }) {
  const styles = {
    info: "border-[var(--sr-link)]/40 bg-[var(--sr-link)]/10 text-[var(--sr-link)]",
    warning: "border-[var(--sr-status-warning)]/40 bg-[var(--sr-status-warning)]/12 text-[var(--sr-status-warning)]",
    error: "border-[var(--sr-action-pressed)]/40 bg-[var(--sr-action-pressed)]/12 text-[var(--sr-action-pressed)]"
  };

  return (
    <div className={`border rounded-[10px] p-[12px_16px] mb-[18px] ${styles[type]}`}>
      {children}
    </div>
  );
}
