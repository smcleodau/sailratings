"use client";

import { SignUp } from "@clerk/nextjs";
import { dark } from "@clerk/themes";

export default function SignUpPage() {
  const clerkAppearance: any = {
    baseTheme: dark,
    variables: {
      colorBackground: 'transparent',
      colorInputBackground: 'rgba(255, 255, 255, 0.05)',
      colorInputText: 'white',
      colorPrimary: '#FF4119',
      colorText: 'white',
      colorTextSecondary: 'rgba(255,255,255,0.7)',
    },
    elements: {
      cardBox: "shadow-none",
      card: "bg-transparent shadow-none w-full",
      logoBox: "hidden",
      headerTitle: "text-white font-display",
      headerSubtitle: "text-white/60",
      formButtonPrimary: "bg-[#FF4119] hover:bg-[#e03a16] text-white rounded-full font-medium transition-all shadow-[0_0_15px_rgba(255,65,25,0.3)]",
      socialButtonsBlockButton: "bg-white/10 border-white/20 hover:bg-white/20 text-white rounded-xl transition-all",
      socialButtonsBlockButtonText: "text-white font-semibold",
      formFieldInput: "bg-white/5 border-white/10 text-white rounded-xl focus:border-[#22b2b8] focus:ring-[#22b2b8]/20 transition-all",
      footerActionLink: "text-[#22b2b8] hover:text-[#1c989c]",
      dividerLine: "bg-white/10",
      dividerText: "text-white/40",
      formFieldLabel: "text-white/80",
      identityPreviewText: "text-white",
      identityPreviewEditButton: "text-[#22b2b8] hover:text-[#1c989c]"
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center relative overflow-hidden bg-[#071324] font-body">
      {/* Dynamic Background Elements */}
      <div className="absolute inset-0 z-0 overflow-hidden pointer-events-none">
        <div className="absolute top-[-20%] left-[-10%] w-[60%] h-[60%] bg-[#0f4c81]/30 rounded-full blur-[120px] mix-blend-screen animate-pulse duration-10000" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[50%] h-[50%] bg-[#22b2b8]/20 rounded-full blur-[100px] mix-blend-screen" />
        <div className="absolute top-[30%] left-[50%] w-[40%] h-[40%] bg-[#ff4119]/10 rounded-full blur-[120px] mix-blend-screen" />
        
        {/* Decorative Grid */}
        <div className="absolute inset-0 bg-[url('https://grainy-gradients.vercel.app/noise.svg')] opacity-20" />
      </div>

      <div className="relative z-10 w-full max-w-md p-6 flex flex-col items-center">
        {/* Brand */}
        <div className="mb-8 flex flex-col items-center">
          <div className="w-12 h-12 bg-white/10 rounded-xl backdrop-blur-md border border-white/20 flex items-center justify-center mb-4 shadow-[0_0_30px_rgba(34,178,184,0.3)]">
            <svg viewBox="0 0 28 28" fill="none" className="w-6 h-6 text-white" aria-hidden="true">
              <path d="M14 2 L14 26" stroke="currentColor" strokeWidth="1.5" />
              <path d="M14 4 L24 20 L14 20 Z" fill="currentColor" opacity="0.5" />
              <path d="M14 8 L6 20 L14 20 Z" fill="currentColor" opacity="0.3" />
            </svg>
          </div>
          <h1 className="text-3xl font-extrabold tracking-tight text-white mb-2 admin-header-font uppercase">
            SAIL<span className="text-[#FF4119]">RATINGS</span>
          </h1>
          <p className="text-[#22b2b8] text-sm font-medium tracking-[0.15em] uppercase admin-mono-font">
            Secure Admin Gateway
          </p>
        </div>
        
        <div className="w-full flex justify-center backdrop-blur-xl bg-white/[0.02] p-1 rounded-2xl border border-white/[0.05] shadow-2xl">
          <SignUp appearance={clerkAppearance} />
        </div>
      </div>
    </div>
  );
}
