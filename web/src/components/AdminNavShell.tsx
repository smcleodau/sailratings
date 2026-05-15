"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { AdminNav } from "@/components/AdminNav";

type RightCtx = {
  setRightSlot: (slot: React.ReactNode | null) => void;
};

const AdminNavRightContext = createContext<RightCtx>({
  setRightSlot: () => {},
});

export function useAdminNavRightSlot(slot: React.ReactNode | null): void {
  const ctx = useContext(AdminNavRightContext);
  useEffect(() => {
    ctx.setRightSlot(slot);
    return () => ctx.setRightSlot(null);
  }, [ctx, slot]);
}

export function AdminNavShell({ children }: { children: React.ReactNode }) {
  const [rightSlot, setRightSlot] = useState<React.ReactNode | null>(null);
  const ctxValue = useMemo<RightCtx>(() => ({ setRightSlot }), []);

  return (
    <div className="min-h-screen bg-navy flex flex-col">
      <AdminNav rightSlot={rightSlot} />
      <AdminNavRightContext.Provider value={ctxValue}>
        {children}
      </AdminNavRightContext.Provider>
    </div>
  );
}
