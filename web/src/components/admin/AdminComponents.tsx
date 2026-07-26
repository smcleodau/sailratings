import React from "react";

export function AdminCard({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`admin-table-container ${className}`}>
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
  const baseStyle = "admin-mono-font text-[10px] tracking-[0.14em] uppercase transition-colors px-4 py-2 rounded-sm disabled:opacity-50 flex items-center justify-center gap-2";
  const variants = {
    primary: "bg-[#0C5F5C] text-[#FBFAF6] hover:bg-[#3E9B95]",
    secondary: "bg-[#E6F0EE] text-[#0C5F5C] hover:bg-[#D1E6E2]",
    outline: "border border-[#0C5F5C]/30 text-[#0C5F5C] hover:bg-[#E6F0EE]"
  };

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`${baseStyle} ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

export function AdminInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`px-4 py-2 bg-[#FFFFFF] border border-[#0C5F5C]/25 text-[#162423] outline-none rounded-sm focus:border-[#0C5F5C] focus:ring-1 focus:ring-[#0C5F5C]/20 transition-all ${props.className || ""}`}
    />
  );
}

export function AdminLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="admin-mono-font text-[10px] tracking-[0.14em] uppercase text-[#7E948F] mb-1 block">
      {children}
    </label>
  );
}

export function AdminAlert({ children, type = "info" }: { children: React.ReactNode; type?: "info" | "warning" | "error" }) {
  const styles = {
    info: "border-[#0C5F5C]/40 bg-[#0C5F5C]/10 text-[#0C5F5C]",
    warning: "border-[#A67C1F]/40 bg-[#E8B23A]/12 text-[#8A6613]",
    error: "border-[#C92B12]/40 bg-[#C92B12]/12 text-[#C92B12]"
  };

  return (
    <div className={`border rounded-[10px] p-[12px_16px] mb-[18px] ${styles[type]}`}>
      {children}
    </div>
  );
}
