"use client";

import React, { useEffect, useState } from "react";
import { adminFetch, getAdminToken } from "@/lib/adminApi";

export default function TelemetryPage() {
    const [token, setToken] = useState<string | null>(null);

    useEffect(() => {
        setToken(getAdminToken());
    }, []);

    if (!token) {
        return <div className="p-6">Requires admin sign-in.</div>;
    }

    return (
        <div className="flex-1 overflow-y-auto bg-[var(--sr-surface-page)] p-6">
            <h1 className="text-2xl font-bold mb-4">Telemetry Dashboard</h1>
            <div className="bg-white p-6 rounded shadow h-[80vh]">
                <iframe src={process.env.NEXT_PUBLIC_JAEGER_UI_URL || "http://localhost:16686"} className="w-full h-full border-0"></iframe>
            </div>
        </div>
    );
}
