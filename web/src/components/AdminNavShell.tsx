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
    <div className="admin-theme">
      <div className="admin-box">
        <AdminNav rightSlot={rightSlot} />
        <AdminNavRightContext.Provider value={ctxValue}>
          <div className="admin-container">
            {children}
          </div>
        </AdminNavRightContext.Provider>
      </div>
    </div>
  );
}
